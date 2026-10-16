from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import daily
from ..config import settings
from ..db import get_db
from ..models import Problem, Submission, UsageDaily, User
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


class UsageReport(BaseModel):
    daily_token_cap: int
    tokens_today: int
    days: list[UsageDay]
    top_users_today: list[TopUser]
    users: int
    problems: int
    submissions_today: int


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
    return UsageReport(
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
