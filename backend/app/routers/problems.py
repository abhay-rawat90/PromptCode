import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from .. import badges, community, crypto, daily, grader, llm, ratelimit, usage
from ..config import settings
from ..security import get_current_user, get_optional_user
from ..db import SessionLocal, get_db
from ..models import Problem, ProblemReport, Submission, TestCase, User, utcnow
from ..schemas import (
    BadgeOut,
    HintSuggestions,
    ProblemDetail,
    ProblemFull,
    ProblemIn,
    ProblemSummary,
    ReportIn,
    RunIn,
    RunOut,
    safe_hints,
    VerifyOut,
    TestCaseIn,
    TestCaseOut,
)

router = APIRouter(prefix="/api/problems", tags=["problems"])
log = logging.getLogger("promptcode.problems")


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:120] or "problem"


def _unique_slug(db: Session, title: str) -> str:
    base = _slugify(title)
    slug, n = base, 2
    while db.scalar(select(Problem.id).where(Problem.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _get_problem(db: Session, slug: str, user: User | None = None) -> Problem:
    """A problem the user may see. Unreleased dailies are hidden from everyone; problems taken
    down by moderation are visible to admins only (so they can review them)."""
    include_hidden = user is not None and user.is_admin
    problem = db.scalar(
        select(Problem)
        .where(Problem.slug == slug, daily.visible_problem_clause(include_hidden=include_hidden))
        .options(selectinload(Problem.test_cases), selectinload(Problem.daily))
    )
    if not problem:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def _can_edit(problem: Problem, user: User | None) -> bool:
    """Authors edit their own problems; admins edit any (built-in and daily ones have no author)."""
    return user is not None and (user.is_admin or problem.author_id == user.id)


def _require_author(problem: Problem, user: User) -> None:
    if not _can_edit(problem, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can modify this problem")


def _require_creator(user: User) -> None:
    if not (settings.allow_user_problems or user.is_admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Creating problems is turned off on this site.")
    if settings.require_verified_to_publish and not user.email_verified and not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Please verify your email before publishing problems. Check your inbox, or resend the link from Settings.",
        )


def _apply(problem: Problem, body: ProblemIn) -> None:
    problem.title = body.title
    problem.description = body.description
    problem.difficulty = body.difficulty
    problem.tags = ",".join(body.tags)
    problem.starter_prompt = body.starter_prompt
    problem.reference_prompt = body.reference_prompt
    problem.hints = body.hints or None
    problem.verified_at = utcnow()  # only called after verification passed
    problem.test_cases = [
        TestCase(position=i, **tc.model_dump()) for i, tc in enumerate(body.test_cases, start=1)
    ]


# ---------- verification (a problem must be solvable before it's published) ----------

NEEDS_REFERENCE = "Add a reference solution prompt. It must pass every test before the problem can be published."


async def _verification(body: ProblemIn, user_id: int, own_key: str | None) -> daily.Verification:
    cases = daily.unsaved_cases(body)
    with usage.attribute("verify", user_id), llm.own_key(own_key):
        result = await daily.check_problem(cases, body.reference_prompt)
    executed = result.reference.executed + (result.trivial.executed if result.trivial else 0)
    if not own_key and executed:
        ratelimit.charge("daily_tests_user", str(user_id), cost=executed)
    return result


async def verify_for_publish(body: ProblemIn, user: User) -> None:
    """Raise unless the problem's reference prompt passes. Usually instant: the author just ran
    the same check with Verify, so every answer comes from the cache."""
    if not body.reference_prompt.strip():
        raise HTTPException(422, NEEDS_REFERENCE)
    key = _user_key(user)
    _precheck("verify_user", user, len(body.test_cases), key)
    result = await _verification(body, user.id, key)
    if result.infra_error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't verify the problem right now: {result.infra_error}")
    if result.problems:
        raise HTTPException(
            422,
            "The problem didn't pass verification. Use Verify to see details.\n" + "\n".join(f"- {p}" for p in result.problems),
        )


@router.post("/verify", response_model=VerifyOut)
async def verify_problem(body: ProblemIn, request: Request, user: User = Depends(get_current_user)):
    """Run the reference prompt over every test (and check a do-nothing prompt fails), showing
    the author full details. Supports the live-progress stream like /run."""
    _require_creator(user)
    if not body.reference_prompt.strip():
        raise HTTPException(422, NEEDS_REFERENCE)
    key = _user_key(user)
    _precheck("verify_user", user, len(body.test_cases), key)
    user_id, internal = user.id, user.is_admin
    cases = daily.unsaved_cases(body)

    async def work() -> VerifyOut:
        result = await _verification(body, user_id, key)
        out = _run_out(result.reference, cases, user_id, key, reveal=True, internal=internal)
        problems = [f"Couldn't finish verifying: {result.infra_error}"] if result.infra_error else result.problems
        return VerifyOut(**out.model_dump(), ok=result.ok, problems=problems)

    return await _respond(request, work, len(cases))


@router.post("/suggest-hints", response_model=HintSuggestions)
async def suggest_hints(body: ProblemIn, user: User = Depends(get_current_user)) -> HintSuggestions:
    """Ask the AI for up to 3 hints for the problem in the form (one model call). The author
    reviews and edits them before saving."""
    _require_creator(user)
    ratelimit.enforce("suggest_hints_user", str(user.id), message="You've asked for a lot of hint suggestions. Try again later.")
    usage.enforce_daily_cap()
    with usage.attribute("other", user.id):
        hints, error = await llm.suggest_hints(body)
    if error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't suggest hints right now: {error}")
    return HintSuggestions(hints=safe_hints(hints, body.description, body.reference_prompt, body.test_cases))


def _solved_ids(db: Session, user: User | None) -> set[int]:
    if user is None:
        return set()
    return set(
        db.scalars(
            select(Submission.problem_id)
            .where(Submission.user_id == user.id, Submission.status == "accepted")
            .distinct()
        )
    )


@router.get("", response_model=list[ProblemSummary])
def list_problems(
    q: str | None = Query(None, max_length=100),
    difficulty: str | None = None,
    tag: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> list[ProblemSummary]:
    stmt = (
        select(Problem)
        .where(daily.visible_problem_clause())
        .options(selectinload(Problem.author), selectinload(Problem.daily))
        .order_by(Problem.id)
    )
    if q:
        escaped = re.sub(r"([\\%_])", r"\\\1", q)  # treat %, _ and \ literally
        stmt = stmt.where(Problem.title.ilike(f"%{escaped}%", escape="\\"))
    if difficulty:
        stmt = stmt.where(Problem.difficulty == difficulty)
    problems = [p for p in db.scalars(stmt) if not tag or tag in p.tag_list]

    stats = {
        pid: (total, accepted)
        for pid, total, accepted in db.execute(
            select(
                Submission.problem_id,
                func.count(),
                func.sum(case((Submission.status == "accepted", 1), else_=0)),
            ).group_by(Submission.problem_id)
        )
    }
    likes = community.like_stats(db)
    solved = _solved_ids(db, user)
    attempted = (
        set(db.scalars(select(Submission.problem_id).where(Submission.user_id == user.id).distinct()))
        if user
        else set()
    )

    out = []
    for p in problems:
        total, accepted = stats.get(p.id, (0, 0))
        out.append(
            ProblemSummary(
                id=p.id,
                slug=p.slug,
                title=p.title,
                difficulty=p.difficulty,
                tags=p.tag_list,
                author=p.author.username if p.author else None,
                solved=p.id in solved,
                attempted=p.id in attempted,
                submissions=total,
                acceptance=round(100 * accepted / total, 1) if total else None,
                like_pct=round(100 * likes[p.id][0] / likes[p.id][1]) if p.id in likes else None,
                ratings=likes[p.id][1] if p.id in likes else 0,
                has_hints=bool(p.hints),
                is_daily=p.daily is not None,
            )
        )
    return out


@router.get("/{slug}", response_model=ProblemDetail)
def get_problem(
    slug: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> ProblemDetail:
    p = _get_problem(db, slug, user)
    samples = [t for t in p.test_cases if t.is_sample]
    return ProblemDetail(
        verified=p.verified_at is not None,
        hidden_reason=(p.hidden_reason or "Hidden by an admin.") if p.hidden_at else None,
        hint_count=len(p.hints or []),
        hints=community.opened_hints(db, user, p),
        **community.rating_summary(db, p.id, user),
        **community.neighbours(db, p),
        id=p.id,
        slug=p.slug,
        title=p.title,
        description=p.description,
        difficulty=p.difficulty,
        tags=p.tag_list,
        starter_prompt=p.starter_prompt,
        author=p.author.username if p.author else None,
        sample_tests=[TestCaseOut.model_validate(t, from_attributes=True) for t in samples],
        hidden_count=len(p.test_cases) - len(samples),
        can_edit=_can_edit(p, user),
        solved=p.id in _solved_ids(db, user),
        daily_day=p.daily.day if p.daily else None,
    )


@router.get("/{slug}/full", response_model=ProblemFull)
def get_problem_full(
    slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemFull:
    p = _get_problem(db, slug, user)
    _require_author(p, user)
    return ProblemFull(
        slug=p.slug,
        title=p.title,
        description=p.description,
        difficulty=p.difficulty,
        tags=p.tag_list,
        starter_prompt=p.starter_prompt,
        reference_prompt=p.reference_prompt or "",
        hints=p.hints or [],
        test_cases=[TestCaseIn.model_validate(t, from_attributes=True) for t in p.test_cases],
    )


@router.post("", response_model=ProblemDetail, status_code=status.HTTP_201_CREATED)
async def create_problem(
    body: ProblemIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemDetail:
    _require_creator(user)
    ratelimit.enforce("create_problem_user", str(user.id), message="You're creating problems too quickly. Try again later.")
    await verify_for_publish(body, user)
    problem = Problem(slug=_unique_slug(db, body.title), author_id=user.id)
    _apply(problem, body)
    db.add(problem)
    db.commit()
    return get_problem(problem.slug, db, user)


@router.put("/{slug}", response_model=ProblemDetail)
async def update_problem(
    slug: str,
    body: ProblemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProblemDetail:
    problem = _get_problem(db, slug, user)
    _require_author(problem, user)
    _require_creator(user)
    await verify_for_publish(body, user)
    _apply(problem, body)
    db.commit()
    return get_problem(slug, db, user)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    problem = _get_problem(db, slug, user)
    _require_author(problem, user)
    db.delete(problem)
    db.commit()


@router.post("/{slug}/report")
def report_problem(
    slug: str, body: ReportIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Flag a broken or unclear problem for an admin. One report per user per problem
    (reporting again updates it)."""
    if not user.email_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify your email to report problems.")
    problem = _get_problem(db, slug, user)
    ratelimit.enforce("report_user", str(user.id), message="You've sent a lot of reports. Please try again later.")
    report = db.scalar(select(ProblemReport).where(ProblemReport.problem_id == problem.id, ProblemReport.user_id == user.id))
    if report is None:
        report = ProblemReport(problem_id=problem.id, user_id=user.id)
        db.add(report)
    report.reason, report.note = body.reason, body.note.strip()
    report.status, report.created_at, report.resolved_at = "open", utcnow(), None
    db.commit()
    return {"detail": "Thanks! An admin will take a look."}


@router.post("/{slug}/run", response_model=RunOut)
async def run_samples(
    slug: str,
    body: RunIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run the prompt against the visible sample tests only. Nothing is saved.
    Send `Accept: application/x-ndjson` to get live progress events before the result."""
    problem = _get_problem(db, slug, user)
    cases = [t for t in problem.test_cases if t.is_sample]
    key = _user_key(user)
    _precheck("run_user", user, len(cases), key)
    user_id, internal = user.id, user.is_admin
    db.close()  # don't hold a database connection while the model works

    async def work() -> RunOut:
        result = await _grade("run", user_id, body.prompt, cases, key)
        return _run_out(result, cases, user_id, key, internal=internal)

    return await _respond(request, work, len(cases))


@router.post("/{slug}/submit", response_model=RunOut)
async def submit(
    slug: str,
    body: RunIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run the prompt against every test case (stopping at the first failure) and record the
    submission. Supports the same live-progress stream as /run."""
    problem = _get_problem(db, slug, user)
    cases = list(problem.test_cases)
    key = _user_key(user)
    _precheck("submit_user", user, len(cases), key)
    user_id, problem_id, internal = user.id, problem.id, user.is_admin
    db.close()

    async def work() -> RunOut:
        result = await _grade("submit", user_id, body.prompt, cases, key)
        out = _run_out(result, cases, user_id, key, internal=internal)
        _save_submission(user_id, problem_id, body.prompt, out)
        return out

    return await _respond(request, work, len(cases))


def _user_key(user: User) -> str | None:
    """The user's own Gemini key, if they saved one (their runs then skip the site's quota)."""
    if not settings.allow_user_keys or not user.gemini_key_enc:
        return None
    return crypto.decrypt(user.gemini_key_enc)


def _precheck(limit: str, user: User, test_count: int, own_key: str | None) -> None:
    ratelimit.enforce(limit, str(user.id), message="You're running prompts too quickly. Wait a moment and try again.")
    if own_key:
        return  # their own quota: the site's budgets don't apply
    usage.enforce_daily_cap()
    ratelimit.check(
        "daily_tests_user",
        str(user.id),
        cost=test_count,
        message="You've used today's test-run budget. It resets within 24 hours.",
    )


async def _grade(kind: str, user_id: int, prompt: str, cases: list[TestCase], own_key: str | None) -> grader.EvalResult:
    with usage.attribute(kind, user_id), llm.own_key(own_key):
        result = await grader.evaluate(prompt, cases, stop_on_failure=kind == "submit")
    if not own_key and result.executed:
        # Only model calls really made count: cached answers and skipped tests are free.
        ratelimit.charge("daily_tests_user", str(user_id), cost=result.executed)
    return result


def _save_submission(user_id: int, problem_id: int, prompt: str, out: RunOut) -> None:
    # A fresh session: the request's own session may already be closed when streaming.
    with SessionLocal() as db:
        submission = Submission(
            user_id=user_id,
            problem_id=problem_id,
            prompt=prompt,
            status=out.status,
            passed=out.passed,
            total=out.total,
            results=[r.model_dump() for r in out.results],
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
        )
        db.add(submission)
        db.commit()
        out.submission_id = submission.id
        if out.status == "accepted":
            user, problem = db.get(User, user_id), db.get(Problem, problem_id)
            if user and problem and daily.record_solve(db, user, problem):
                out.streak_extended = True
                out.current_streak = user.current_streak
            if user:
                out.new_badges = [BadgeOut(**b) for b in badges.evaluate(db, user)]


def _run_out(
    result: grader.EvalResult, cases: list[TestCase], user_id: int, own_key: str | None, reveal: bool = False,
    internal: bool = False,
) -> RunOut:
    """`internal` (admins) adds which model answered and cache hits; solvers never see those."""
    by_id = {c.id: c for c in cases}
    return RunOut(
        daily_remaining=None if own_key else ratelimit.remaining("daily_tests_user", str(user_id)),
        status=result.status,
        passed=result.passed,
        total=len(result.cases),
        results=[r.public(by_id[r.test_id], reveal, internal) for r in result.cases],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        backup_models=result.backup_models if internal else [],
        cached=sum(c.cached for c in result.cases) if internal else 0,
        skipped=sum(c.skipped for c in result.cases),
        own_key=bool(own_key),
    )


STREAM_TYPE = "application/x-ndjson"
HEARTBEAT_SECONDS = 15.0


async def _respond(request: Request, work: Callable[[], Awaitable[RunOut]], total: int):
    """Plain JSON, or - if the client accepts NDJSON - a stream of progress events
    ({"type": "progress" | "waiting" | "ping"}) ending in {"type": "result", "data": RunOut}
    or {"type": "error", "status": ..., "detail": ...}."""
    if STREAM_TYPE not in request.headers.get("accept", ""):
        return await work()

    events: asyncio.Queue[dict] = asyncio.Queue()

    def on_event(event: dict) -> None:
        # Which model is busy is a behind-the-scenes detail: solvers only need "queued, ~12s".
        events.put_nowait({k: v for k, v in event.items() if k != "model"})

    async def run() -> RunOut:
        with llm.listen(on_event):
            return await work()

    # Started now (not when the client reads) and kept running if the client disconnects,
    # so a submission is always graded and saved once it's accepted.
    task = asyncio.create_task(run())
    # If the client left before the result, still collect the outcome so nothing is lost silently.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def stream():
        yield _line({"type": "start", "total": total})
        while not task.done():
            getter = asyncio.ensure_future(events.get())
            finished, _ = await asyncio.wait({getter, task}, timeout=HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if getter in finished:
                yield _line(getter.result())
            else:
                getter.cancel()
                if not finished:
                    yield _line({"type": "ping"})  # keeps proxies from closing an idle connection
        while not events.empty():
            yield _line(events.get_nowait())
        try:
            yield _line({"type": "result", "data": task.result().model_dump(mode="json")})
        except HTTPException as exc:
            yield _line({"type": "error", "status": exc.status_code, "detail": exc.detail})
        except Exception:  # noqa: BLE001
            log.exception("Grading failed")
            yield _line({"type": "error", "status": 500, "detail": "Something went wrong while grading. Please try again."})

    return StreamingResponse(
        stream(), media_type=STREAM_TYPE, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


def _line(event: dict) -> str:
    return json.dumps(event, separators=(",", ":")) + "\n"
