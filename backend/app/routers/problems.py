import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from .. import crypto, daily, grader, llm, ratelimit, usage
from ..config import settings
from ..security import get_current_user, get_optional_user
from ..db import SessionLocal, get_db
from ..models import Problem, Submission, TestCase, User
from ..schemas import (
    ProblemDetail,
    ProblemFull,
    ProblemIn,
    ProblemSummary,
    RunIn,
    RunOut,
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


def _get_problem(db: Session, slug: str) -> Problem:
    problem = db.scalar(
        select(Problem)
        .where(Problem.slug == slug, daily.visible_problem_clause())  # unreleased dailies stay hidden
        .options(selectinload(Problem.test_cases), selectinload(Problem.daily))
    )
    if not problem:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def _require_author(problem: Problem, user: User) -> None:
    if problem.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can modify this problem")


def _apply(problem: Problem, body: ProblemIn) -> None:
    problem.title = body.title
    problem.description = body.description
    problem.difficulty = body.difficulty
    problem.tags = ",".join(body.tags)
    problem.starter_prompt = body.starter_prompt
    problem.test_cases = [
        TestCase(position=i, **tc.model_dump()) for i, tc in enumerate(body.test_cases, start=1)
    ]


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
        .options(selectinload(Problem.author))
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
            )
        )
    return out


@router.get("/{slug}", response_model=ProblemDetail)
def get_problem(
    slug: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> ProblemDetail:
    p = _get_problem(db, slug)
    samples = [t for t in p.test_cases if t.is_sample]
    return ProblemDetail(
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
        can_edit=user is not None and p.author_id == user.id,
        solved=p.id in _solved_ids(db, user),
        daily_day=p.daily.day if p.daily else None,
    )


@router.get("/{slug}/full", response_model=ProblemFull)
def get_problem_full(
    slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemFull:
    p = _get_problem(db, slug)
    _require_author(p, user)
    return ProblemFull(
        slug=p.slug,
        title=p.title,
        description=p.description,
        difficulty=p.difficulty,
        tags=p.tag_list,
        starter_prompt=p.starter_prompt,
        test_cases=[TestCaseIn.model_validate(t, from_attributes=True) for t in p.test_cases],
    )


@router.post("", response_model=ProblemDetail, status_code=status.HTTP_201_CREATED)
def create_problem(
    body: ProblemIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProblemDetail:
    if settings.require_verified_to_publish and not user.email_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Please verify your email before publishing problems. Check your inbox, or resend the link from Settings.",
        )
    ratelimit.enforce("create_problem_user", str(user.id), message="You're creating problems too quickly. Try again later.")
    problem = Problem(slug=_unique_slug(db, body.title), author_id=user.id)
    _apply(problem, body)
    db.add(problem)
    db.commit()
    return get_problem(problem.slug, db, user)


@router.put("/{slug}", response_model=ProblemDetail)
def update_problem(
    slug: str,
    body: ProblemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProblemDetail:
    problem = _get_problem(db, slug)
    _require_author(problem, user)
    _apply(problem, body)
    db.commit()
    return get_problem(slug, db, user)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    problem = _get_problem(db, slug)
    _require_author(problem, user)
    db.delete(problem)
    db.commit()


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
    problem = _get_problem(db, slug)
    cases = [t for t in problem.test_cases if t.is_sample]
    key = _user_key(user)
    _precheck("run_user", user, len(cases), key)
    user_id = user.id
    db.close()  # don't hold a database connection while the model works

    async def work() -> RunOut:
        result = await _grade("run", user_id, body.prompt, cases, key)
        return _run_out(result, cases, user_id, key)

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
    problem = _get_problem(db, slug)
    cases = list(problem.test_cases)
    key = _user_key(user)
    _precheck("submit_user", user, len(cases), key)
    user_id, problem_id = user.id, problem.id
    db.close()

    async def work() -> RunOut:
        result = await _grade("submit", user_id, body.prompt, cases, key)
        out = _run_out(result, cases, user_id, key)
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


def _run_out(result: grader.EvalResult, cases: list[TestCase], user_id: int, own_key: str | None) -> RunOut:
    by_id = {c.id: c for c in cases}
    return RunOut(
        daily_remaining=None if own_key else ratelimit.remaining("daily_tests_user", str(user_id)),
        status=result.status,
        passed=result.passed,
        total=len(result.cases),
        results=[r.public(by_id[r.test_id]) for r in result.cases],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        backup_models=result.backup_models,
        cached=sum(c.cached for c in result.cases),
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

    async def run() -> RunOut:
        with llm.listen(events.put_nowait):
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
