"""Daily challenge: one new problem per UTC day, plus streaks.

Each day's problem comes from the curated queue if it has items, otherwise Gemini writes one.
An AI problem is only published after verification:
  1. it passes the same validation as a user-created problem,
  2. its reference prompt passes every test case, and
  3. a do-nothing prompt (just "{{input}}") does NOT pass them all.

Problems are prepared a day ahead and stay hidden until their day starts (`available_from`).
Run `python -m app.daily` to prepare today's and tomorrow's problems from a cron job.
"""

import asyncio
import logging
import random
import re
from datetime import date, datetime, time, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import grader, llm
from .config import settings
from .db import SessionLocal
from .models import DailyChallenge, DailyQueueItem, DailySolve, Problem, TestCase, User
from .schemas import ProblemIn

log = logging.getLogger("promptcode.daily")

# Weekly rhythm: gentle start, harder midweek, hard on Friday.
DIFFICULTY_BY_WEEKDAY = ["easy", "medium", "medium", "hard", "hard", "medium", "easy"]
THEMES = [
    "strict output formats (exact labels, single tokens, fixed templates)",
    "information extraction into JSON",
    "classification with tricky edge cases",
    "rewriting text under hard constraints (length, tone, banned words)",
    "robustness against prompt injection hidden in the input",
    "multi-step reasoning that must output only a final answer",
    "data transformation (units, dates, lists, tables)",
    "persona and tone control while staying accurate",
    "summarization that must keep specific facts",
    "refusing or deflecting out-of-scope requests politely",
    "generating structured lists with exact counts",
    "normalizing messy user input into a canonical form",
]

_generation_lock = asyncio.Lock()


# ---------- time helpers (monkeypatched in tests) ----------

def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def next_reset() -> datetime:
    return day_start(today() + timedelta(days=1))


def visible_problem_clause():
    """SQL filter: problems that are already public."""
    return (Problem.available_from.is_(None)) | (Problem.available_from <= now())


# ---------- publishing ----------

def _slug_for(db: Session, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:110] or "daily"
    slug, n = base, 2
    while db.scalar(select(Problem.id).where(Problem.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _build_problem(db: Session, spec: ProblemIn, day: date) -> Problem:
    tags = list(dict.fromkeys([*spec.tags, "daily"]))[:8]
    problem = Problem(
        slug=_slug_for(db, spec.title),
        title=spec.title,
        description=spec.description,
        difficulty=spec.difficulty,
        tags=",".join(tags),
        starter_prompt=spec.starter_prompt,
        available_from=day_start(day),
    )
    problem.test_cases = [TestCase(position=i, **tc.model_dump()) for i, tc in enumerate(spec.test_cases, start=1)]
    return problem


def _publish(db: Session, spec: ProblemIn, day: date, source: str) -> DailyChallenge | None:
    problem = _build_problem(db, spec, day)
    daily = DailyChallenge(day=day, problem=problem, source=source)
    db.add(daily)
    try:
        db.commit()
    except IntegrityError:  # another worker published this day first
        db.rollback()
        return None
    log.info("Published daily %s: %r (%s)", day, problem.title, source)
    return daily


# ---------- AI generation + verification ----------

def _brief(day: date, feedback: str | None) -> str:
    with SessionLocal() as db:
        titles = db.scalars(select(Problem.title).order_by(Problem.id.desc()).limit(80)).all()
    rng = random.Random(day.toordinal())
    theme = THEMES[day.toordinal() % len(THEMES)]
    parts = [
        f"Date: {day.isoformat()}.",
        f"Difficulty: {DIFFICULTY_BY_WEEKDAY[day.weekday()]}.",
        f"Theme to explore: {theme}.",
        f"Random seed for variety: {rng.randint(1000, 9999)}.",
        "Existing titles (do not repeat or closely imitate):\n- " + "\n- ".join(titles),
    ]
    if feedback:
        parts.append(f"Your previous attempt was rejected. Fix these problems:\n{feedback}")
    return "\n\n".join(parts)


def _unsaved_cases(spec: ProblemIn) -> list[TestCase]:
    return [TestCase(id=i, position=i, **tc.model_dump()) for i, tc in enumerate(spec.test_cases, start=1)]


class GenerationAborted(Exception):
    """Stop generating for now (quota used up, API down): more attempts would only waste quota."""


async def verify(spec: ProblemIn, reference_prompt: str) -> str | None:
    """Return None if the problem is good, else a description of what's wrong (for the generator
    to fix). Raises GenerationAborted when verification itself couldn't run."""
    cases = _unsaved_cases(spec)
    sample, hidden = sum(c.is_sample for c in cases), sum(not c.is_sample for c in cases)
    if not (4 <= len(cases) <= 8) or sample < 1 or hidden < 2:
        return f"Need 4-8 tests with at least 1 sample and 2 hidden (got {sample} sample, {hidden} hidden)."

    reference = await grader.evaluate(reference_prompt, cases)
    if any(c.error for c in reference.cases):
        raise GenerationAborted("; ".join(sorted({c.detail or "error" for c in reference.cases if c.error})))
    failing = [c for c in reference.cases if not c.passed]
    if failing:
        lines = [
            f"- test {c.position} ({c.check_type}): {c.detail or 'failed'}; reference output was: {c.output[:300]!r}"
            for c in failing
        ]
        return "The reference prompt failed these tests (fix the tests or the prompt):\n" + "\n".join(lines)

    trivial = await grader.evaluate(llm.INPUT_PLACEHOLDER, cases)
    if any(c.error for c in trivial.cases):
        raise GenerationAborted("; ".join(sorted({c.detail or "error" for c in trivial.cases if c.error})))
    if trivial.passed == len(cases):
        return "A prompt that just repeats the input passes every test - the problem is too trivial."
    return None


def _title_taken(title: str) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Problem.id).where(func.lower(Problem.title) == title.lower())) is not None


async def generate_verified(day: date) -> ProblemIn | None:
    """Generate and verify a problem. Uses only short DB sessions, since this takes minutes."""
    feedback = None
    for attempt in range(1, settings.daily_generation_attempts + 1):
        generated, error, usage = await llm.generate_problem(_brief(day, feedback))
        if generated is None:
            if usage.quota_exhausted:
                raise GenerationAborted(error or "quota exhausted")
            log.warning("Daily %s attempt %d: generation failed: %s", day, attempt, error)
            feedback = None
            continue
        try:
            spec = ProblemIn(
                title=generated.title.strip()[:120],
                description=generated.description,
                difficulty=generated.difficulty,
                tags=generated.tags,
                starter_prompt=generated.starter_prompt,
                test_cases=[t.model_dump() for t in generated.test_cases],
            )
        except ValidationError as exc:
            feedback = "Validation errors:\n" + "\n".join(f"- {e['loc']}: {e['msg']}" for e in exc.errors())
            log.warning("Daily %s attempt %d rejected: %s", day, attempt, feedback)
            continue
        if _title_taken(spec.title):
            feedback = f"The title {spec.title!r} already exists. Pick a different problem."
            continue
        feedback = await verify(spec, generated.reference_prompt)
        if feedback is None:
            log.info("Daily %s attempt %d verified: %r", day, attempt, spec.title)
            return spec
        log.warning("Daily %s attempt %d failed verification: %s", day, attempt, feedback)
    return None


# After a failed generation, wait before trying again so a broken model or an exhausted quota
# isn't hammered every scheduler tick (or on every server restart in development).
RETRY_AFTER_FAILURE = timedelta(minutes=30)
RETRY_AFTER_QUOTA = timedelta(hours=2)
_retry_not_before: dict[date, datetime] = {}


def generation_status(day: date) -> str:
    """'preparing' while a problem for `day` may still appear soon, 'unavailable' after failures."""
    not_before = _retry_not_before.get(day)
    return "unavailable" if not_before and now() < not_before else "preparing"


async def ensure_daily(day: date) -> DailyChallenge | None:
    """Make sure `day` has a daily problem. Safe to call repeatedly and concurrently."""
    if not settings.daily_enabled:
        return None
    # Nobody is waiting on this, so ride out free-tier rate limits instead of failing.
    with llm.patience(300):
        return await _ensure_daily(day)


async def _ensure_daily(day: date) -> DailyChallenge | None:
    async with _generation_lock:
        with SessionLocal() as db:
            existing = db.scalar(select(DailyChallenge).where(DailyChallenge.day == day))
            if existing:
                return existing
            queued = db.scalar(
                select(DailyQueueItem).where(DailyQueueItem.used_on.is_(None)).order_by(DailyQueueItem.id)
            )
            if queued:
                queued.used_on = day
                return _publish(db, ProblemIn.model_validate(queued.payload), day, "queue")

        if not settings.gemini_api_key:
            log.warning("Daily %s: queue is empty and GEMINI_API_KEY is not set; skipping.", day)
            return None
        if day in _retry_not_before and now() < _retry_not_before[day]:
            return None  # backing off after a recent failure

        log.info("Daily %s: generating a new problem with %s", day, settings.daily_model)
        try:
            spec = await generate_verified(day)
        except GenerationAborted as exc:
            _retry_not_before[day] = now() + RETRY_AFTER_QUOTA
            log.error("Daily %s: generation stopped (%s). Next try after %s.", day, exc, _retry_not_before[day])
            return None
        if spec is None:
            _retry_not_before[day] = now() + RETRY_AFTER_FAILURE
            log.error("Daily %s: no verified problem after %d attempts. Next try after %s.",
                      day, settings.daily_generation_attempts, _retry_not_before[day])
            return None
        _retry_not_before.pop(day, None)
        with SessionLocal() as db:
            return _publish(db, spec, day, "ai")


async def ensure_upcoming() -> None:
    """Prepare today's problem, then tomorrow's (so it's ready at midnight)."""
    for day in (today(), today() + timedelta(days=1)):
        try:
            await ensure_daily(day)
        except Exception:  # noqa: BLE001 - never let the scheduler die
            log.exception("Daily %s: unexpected error", day)


async def scheduler(interval_seconds: int = 600) -> None:
    while True:
        await ensure_upcoming()
        await asyncio.sleep(interval_seconds)


# ---------- streaks ----------

def effective_streak(user: User) -> int:
    """Current streak, counting it as broken if yesterday's daily was missed."""
    if user.last_daily_solved is None or user.last_daily_solved < today() - timedelta(days=1):
        return 0
    return user.current_streak


def record_solve(db: Session, user: User, problem: Problem) -> bool:
    """Call after an accepted submission. Returns True if it extended the user's streak."""
    daily = problem.daily
    day = today()
    if daily is None or daily.day != day:
        return False  # not today's daily (old dailies still count as solved, just not for streaks)
    if db.scalar(select(DailySolve.id).where(DailySolve.user_id == user.id, DailySolve.day == day)):
        return False
    db.add(DailySolve(user_id=user.id, day=day))
    user.current_streak = user.current_streak + 1 if user.last_daily_solved == day - timedelta(days=1) else 1
    user.best_streak = max(user.best_streak, user.current_streak)
    user.last_daily_solved = day
    try:
        db.commit()
    except IntegrityError:  # double-submit race
        db.rollback()
        return False
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(ensure_upcoming())
