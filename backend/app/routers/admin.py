from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import daily, llm
from ..config import settings
from ..db import get_db
from ..models import ModelUsageDaily, Problem, Submission, UsageDaily, User
from ..security import get_admin_user

router = APIRouter(prefix="/api/admin", tags=["admin"])

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
