import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session, selectinload

from .. import daily, llm
from ..config import settings
from ..db import get_db
from ..models import ModelUsageDaily, Problem, ProblemRating, ProblemReport, Submission, UsageDaily, User, utcnow
from ..security import get_admin_user

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger("promptcode.admin")

KINDS = ("run", "submit", "daily", "other")


class UsageDay(BaseModel):
    day: str
    requests: int
    tokens: dict[str, int]  # kind -> input + output tokens
    total: int


class TopUser(BaseModel):
    username: str
    requests: int
    tokens: int


class ModelQuota(BaseModel):
    model: str  # "provider:model"
    provider: str
    name: str
    roles: list[str]  # e.g. ["run"], ["judge backup", "daily backup"]
    has_key: bool
    rpm: int  # pacing limits (0 = unknown / none)
    tpm: int
    rpd: int
    used_last_minute: int
    remaining_today: int | None  # as reported by the provider (Groq)
    requests_today: int  # UTC day, this server's count
    tokens_today: int
    cache_hits_today: int
    failures_today: int
    status: str  # ok | busy | exhausted | no_key
    blocked_until: datetime | None
    block_reason: str | None


class UsageReport(BaseModel):
    daily_token_cap: int
    tokens_today: int
    days: list[UsageDay]
    top_users_today: list[TopUser]
    users: int
    problems: int
    submissions_today: int
    models: list[ModelQuota] = []
    warnings: list[str] = []
    cache_hits_today: int = 0
    users_with_own_key: int = 0
    open_reports: int = 0


# ---------- reports & moderation ----------

class ReportOut(BaseModel):
    username: str
    reason: str
    note: str
    status: str
    created_at: datetime


class ReportedProblem(BaseModel):
    problem_id: int
    slug: str
    title: str
    daily_day: date | None
    hidden: bool
    hidden_reason: str | None
    open_reports: int
    reports: list[ReportOut]


class HideIn(BaseModel):
    reason: str = Field(default="", max_length=300)


@router.get("/reports", response_model=list[ReportedProblem])
def list_reports(db: Session = Depends(get_db), _: User = Depends(get_admin_user)) -> list[ReportedProblem]:
    """Problems with open reports, plus problems currently hidden. Newest report first."""
    problem_ids = set(db.scalars(select(ProblemReport.problem_id).where(ProblemReport.status == "open")))
    problem_ids |= set(db.scalars(select(Problem.id).where(Problem.hidden_at.is_not(None))))
    if not problem_ids:
        return []
    problems = db.scalars(
        select(Problem).where(Problem.id.in_(problem_ids)).options(selectinload(Problem.daily))
    ).all()
    reports = db.scalars(
        select(ProblemReport)
        .where(ProblemReport.problem_id.in_(problem_ids))
        .options(selectinload(ProblemReport.user))
        .order_by(ProblemReport.created_at.desc())
    ).all()
    by_problem: dict[int, list[ProblemReport]] = {}
    for r in reports:
        by_problem.setdefault(r.problem_id, []).append(r)

    out = [
        ReportedProblem(
            problem_id=p.id,
            slug=p.slug,
            title=p.title,
            daily_day=p.daily.day if p.daily else None,
            hidden=p.hidden_at is not None,
            hidden_reason=p.hidden_reason,
            open_reports=sum(r.status == "open" for r in by_problem.get(p.id, [])),
            reports=[
                ReportOut(username=r.user.username, reason=r.reason, note=r.note, status=r.status, created_at=r.created_at)
                for r in by_problem.get(p.id, [])
            ],
        )
        for p in problems
    ]
    out.sort(key=lambda g: (g.open_reports == 0, -max((r.created_at.timestamp() for r in g.reports), default=0)))
    return out


def _admin_problem(db: Session, problem_id: int) -> Problem:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def _close_reports(db: Session, problem_id: int, new_status: str) -> None:
    db.execute(
        sa_update(ProblemReport)
        .where(ProblemReport.problem_id == problem_id, ProblemReport.status == "open")
        .values(status=new_status, resolved_at=utcnow())
    )


@router.post("/problems/{problem_id}/dismiss-reports", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_reports(problem_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)) -> None:
    """The reports were wrong: close them and keep the problem up."""
    _admin_problem(db, problem_id)
    _close_reports(db, problem_id, "dismissed")
    db.commit()


@router.post("/problems/{problem_id}/hide", status_code=status.HTTP_204_NO_CONTENT)
def hide_problem(problem_id: int, body: HideIn, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)) -> None:
    """Take a problem down (kept in the database with its submissions; can be restored)."""
    problem = _admin_problem(db, problem_id)
    problem.hidden_at = utcnow()
    problem.hidden_reason = body.reason.strip() or None
    _close_reports(db, problem_id, "resolved")
    db.commit()
    log.info("Admin %s hid problem %s (%r): %s", admin.username, problem.id, problem.title, problem.hidden_reason)


@router.post("/problems/{problem_id}/unhide", status_code=status.HTTP_204_NO_CONTENT)
def unhide_problem(problem_id: int, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)) -> None:
    problem = _admin_problem(db, problem_id)
    problem.hidden_at = None
    problem.hidden_reason = None
    db.commit()
    log.info("Admin %s restored problem %s (%r)", admin.username, problem.id, problem.title)


# ---------- solver feedback ----------

# A problem is flagged once this many solvers agree.
MIN_FEEDBACK_VOTES = 3


class ProblemFeedback(BaseModel):
    slug: str
    title: str
    difficulty: str
    likes: int
    dislikes: int
    easier: int
    right: int
    harder: int
    flags: list[str]


@router.get("/problem-feedback", response_model=list[ProblemFeedback])
def problem_feedback(db: Session = Depends(get_db), _: User = Depends(get_admin_user)) -> list[ProblemFeedback]:
    """Problems solvers dislike, or whose difficulty label they disagree with."""
    def count(condition):
        return func.sum(case((condition, 1), else_=0))

    rows = db.execute(
        select(
            Problem.slug, Problem.title, Problem.difficulty,
            count(ProblemRating.liked.is_(True)), count(ProblemRating.liked.is_(False)),
            count(ProblemRating.difficulty == "easier"), count(ProblemRating.difficulty == "right"),
            count(ProblemRating.difficulty == "harder"),
        )
        .join(ProblemRating, ProblemRating.problem_id == Problem.id)
        .where(Problem.hidden_at.is_(None))
        .group_by(Problem.id)
    ).all()
    out = []
    for slug, title, difficulty, likes, dislikes, easier, right, harder in rows:
        flags = []
        if dislikes >= MIN_FEEDBACK_VOTES and dislikes > likes:
            flags.append("Mostly disliked")
        votes = easier + right + harder
        if harder >= MIN_FEEDBACK_VOTES and harder * 2 > votes:
            flags.append(f"Harder than '{difficulty}'")
        if easier >= MIN_FEEDBACK_VOTES and easier * 2 > votes:
            flags.append(f"Easier than '{difficulty}'")
        if flags:
            out.append(ProblemFeedback(slug=slug, title=title, difficulty=difficulty, likes=likes, dislikes=dislikes,
                                       easier=easier, right=right, harder=harder, flags=flags))
    out.sort(key=lambda f: f.dislikes - f.likes, reverse=True)
    return out


@router.post("/solutions/{submission_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_shared_solution(submission_id: int, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)) -> None:
    """Take an inappropriate shared solution out of the Solutions tab (the submission itself stays)."""
    submission = db.get(Submission, submission_id)
    if submission is None or not submission.shared:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solution not found")
    submission.shared = False
    db.commit()
    log.info("Admin %s removed shared solution %s", admin.username, submission_id)


def _model_quotas(db: Session, today) -> tuple[list[ModelQuota], list[str]]:
    counts = {
        row.model: row
        for row in db.scalars(select(ModelUsageDaily).where(ModelUsageDaily.day == today))
    }
    models, warnings = [], []
    for info in llm.model_status():
        row = counts.get(info["model"])
        requests = row.requests if row else 0
        blocked_until = info.pop("blocked_until")
        if not info["has_key"]:
            state = "no_key"
        elif blocked_until:
            state = "exhausted"
        elif info["rpm"] and info["used_last_minute"] >= info["rpm"]:
            state = "busy"
        else:
            state = "ok"
        quota_row = ModelQuota(
            **info,
            requests_today=requests,
            tokens_today=(row.input_tokens + row.output_tokens) if row else 0,
            cache_hits_today=row.cache_hits if row else 0,
            failures_today=row.failures if row else 0,
            status=state,
            blocked_until=datetime.fromtimestamp(blocked_until, timezone.utc) if blocked_until else None,
        )
        models.append(quota_row)

        name = llm.display_name(quota_row.model)
        main_roles = [r for r in quota_row.roles if "backup" not in r]
        if state == "no_key" and main_roles:
            warnings.append(f"{name} ({', '.join(main_roles)}) has no API key configured.")
        elif state == "exhausted":
            until = quota_row.blocked_until.strftime("%H:%M UTC") if quota_row.blocked_until else "later"
            warnings.append(f"{name} is out of quota until about {until} - backups are being used.")
        elif quota_row.rpd:
            left = quota_row.remaining_today if quota_row.remaining_today is not None else quota_row.rpd - requests
            if left <= quota_row.rpd * 0.2:
                warnings.append(f"{name} has used about {100 - 100 * max(left, 0) // quota_row.rpd}% of its daily quota.")

    for role, chain in (("Running prompts", llm.run_chain()), ("Judging", llm.judge_chain())):
        if not any(llm.has_key(ref) for ref in chain[1:]):
            warnings.append(f"{role} has no backup model with an API key - add GROQ_API_KEY for free backups.")
    return models, warnings


@router.get("/usage", response_model=UsageReport)
def usage_report(
    days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db), _: User = Depends(get_admin_user)
) -> UsageReport:
    today = daily.today()
    since = today - timedelta(days=days - 1)
    tokens = UsageDaily.input_tokens + UsageDaily.output_tokens

    per_day: dict = {since + timedelta(days=i): UsageDay(day=(since + timedelta(days=i)).isoformat(), requests=0,
                                                       tokens=dict.fromkeys(KINDS, 0), total=0) for i in range(days)}
    rows = db.execute(
        select(UsageDaily.day, UsageDaily.kind, func.sum(UsageDaily.requests), func.sum(tokens))
        .where(UsageDaily.day >= since)
        .group_by(UsageDaily.day, UsageDaily.kind)
    )
    for day, kind, requests, spent in rows:
        entry = per_day[day]
        entry.requests += requests
        entry.tokens[kind if kind in KINDS else "other"] += spent
        entry.total += spent

    top = db.execute(
        select(User.username, func.sum(UsageDaily.requests), func.sum(tokens))
        .join(User, User.id == UsageDaily.user_id)
        .where(UsageDaily.day == today)
        .group_by(User.username)
        .order_by(func.sum(tokens).desc())
        .limit(10)
    )
    start_of_today = daily.day_start(today)
    models, warnings = _model_quotas(db, today)
    return UsageReport(
        models=models,
        warnings=warnings,
        cache_hits_today=sum(m.cache_hits_today for m in models),
        open_reports=db.scalar(select(func.count()).select_from(ProblemReport).where(ProblemReport.status == "open")) or 0,
        users_with_own_key=db.scalar(select(func.count()).select_from(User).where(User.gemini_key_enc.is_not(None))) or 0,
        daily_token_cap=settings.daily_token_cap,
        tokens_today=per_day[today].total,
        days=list(reversed(per_day.values())),
        top_users_today=[TopUser(username=u, requests=r, tokens=t) for u, r, t in top],
        users=db.scalar(select(func.count()).select_from(User)) or 0,
        problems=db.scalar(select(func.count()).select_from(Problem).where(daily.visible_problem_clause())) or 0,
        submissions_today=db.scalar(
            select(func.count()).select_from(Submission).where(Submission.created_at >= start_of_today)
        ) or 0,
    )
