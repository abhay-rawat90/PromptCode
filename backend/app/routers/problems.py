import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from .. import daily, grader, ratelimit, usage
from ..config import settings
from ..security import get_current_user, get_optional_user
from ..db import get_db
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
    slug: str, body: RunIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> RunOut:
    """Run the prompt against the visible sample tests only. Nothing is saved."""
    problem = _get_problem(db, slug)
    cases = [t for t in problem.test_cases if t.is_sample]
    _spend_budget("run_user", user, len(cases))
    with usage.attribute("run", user.id):
        result = await grader.evaluate(body.prompt, cases)
    return _run_out(result, cases, user)


@router.post("/{slug}/submit", response_model=RunOut)
async def submit(
    slug: str, body: RunIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> RunOut:
    """Run the prompt against every test case and record the submission."""
    problem = _get_problem(db, slug)
    cases = list(problem.test_cases)
    _spend_budget("submit_user", user, len(cases))
    with usage.attribute("submit", user.id):
        result = await grader.evaluate(body.prompt, cases)
    out = _run_out(result, cases, user)

    submission = Submission(
        user_id=user.id,
        problem_id=problem.id,
        prompt=body.prompt,
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
    if out.status == "accepted" and daily.record_solve(db, user, problem):
        out.streak_extended = True
        out.current_streak = user.current_streak
    return out


def _spend_budget(limit: str, user: User, test_count: int) -> None:
    usage.enforce_daily_cap()
    ratelimit.enforce(limit, str(user.id), message="You're running prompts too quickly. Wait a moment and try again.")
    ratelimit.enforce(
        "daily_tests_user",
        str(user.id),
        cost=test_count,
        message="You've used today's test-run budget. It resets within 24 hours.",
    )


def _run_out(result: grader.EvalResult, cases: list[TestCase], user: User) -> RunOut:
    by_id = {c.id: c for c in cases}
    return RunOut(
        daily_remaining=ratelimit.remaining("daily_tests_user", str(user.id)),
        status=result.status,
        passed=result.passed,
        total=len(result.cases),
        results=[r.public(by_id[r.test_id]) for r in result.cases],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
