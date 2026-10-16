import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import daily
from ..config import settings
from ..db import get_db
from ..models import DailyChallenge, DailyQueueItem, DailySolve, Problem, Submission, User
from ..schemas import DailyArchiveItem, DailyProblemOut, DailyTodayOut, ProblemIn, QueueItemOut
from ..security import get_admin_user, get_optional_user

router = APIRouter(prefix="/api", tags=["daily"])

# Strong reference so the background task isn't garbage-collected mid-run.
_pending: set[asyncio.Task] = set()


def _kick_off_preparation() -> None:
    if not any(not t.done() for t in _pending):
        task = asyncio.create_task(daily.ensure_daily(daily.today()))
        _pending.add(task)
        task.add_done_callback(_pending.discard)


def _problem_out(p: Problem) -> DailyProblemOut:
    return DailyProblemOut(slug=p.slug, title=p.title, difficulty=p.difficulty, tags=p.tag_list)


@router.get("/daily/today", response_model=DailyTodayOut)
async def daily_today(db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)) -> DailyTodayOut:
    day = daily.today()
    out = DailyTodayOut(status="disabled", day=day, next_reset=daily.next_reset())
    if user:
        out.current_streak = daily.effective_streak(user)
        out.best_streak = user.best_streak
    if not settings.daily_enabled:
        return out

    challenge = db.scalar(
        select(DailyChallenge).where(DailyChallenge.day == day).options(selectinload(DailyChallenge.problem))
    )
    if challenge is None:
        # The scheduler normally prepares it ahead of time; this covers a cold start.
        _kick_off_preparation()
        out.status = daily.generation_status(day)
        return out

    out.status = "ready"
    out.problem = _problem_out(challenge.problem)
    if user:
        out.solved_today = db.scalar(
            select(DailySolve.id).where(DailySolve.user_id == user.id, DailySolve.day == day)
        ) is not None
    return out


@router.get("/daily/archive", response_model=list[DailyArchiveItem])
def daily_archive(
    limit: int = Query(60, ge=1, le=366),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> list[DailyArchiveItem]:
    rows = db.scalars(
        select(DailyChallenge)
        .where(DailyChallenge.day <= daily.today())
        .options(selectinload(DailyChallenge.problem))
        .order_by(DailyChallenge.day.desc())
        .limit(limit)
    ).all()
    on_day: set = set()
    solved_ids: set[int] = set()
    if user:
        on_day = set(db.scalars(select(DailySolve.day).where(DailySolve.user_id == user.id)))
        solved_ids = set(
            db.scalars(
                select(Submission.problem_id)
                .where(Submission.user_id == user.id, Submission.status == "accepted")
                .distinct()
            )
        )
    return [
        DailyArchiveItem(
            day=c.day,
            problem=_problem_out(c.problem),
            solved_on_day=c.day in on_day,
            solved=c.problem_id in solved_ids,
        )
        for c in rows
    ]


# ---------- admin: curated queue ----------

@router.get("/admin/daily/queue", response_model=list[QueueItemOut])
def list_queue(db: Session = Depends(get_db), _: User = Depends(get_admin_user)) -> list[QueueItemOut]:
    items = db.scalars(select(DailyQueueItem).order_by(DailyQueueItem.used_on.is_not(None), DailyQueueItem.id)).all()
    return [
        QueueItemOut(
            id=i.id,
            title=i.payload["title"],
            difficulty=i.payload["difficulty"],
            created_at=i.created_at,
            used_on=i.used_on,
        )
        for i in items
    ]


@router.post("/admin/daily/queue", response_model=QueueItemOut, status_code=status.HTTP_201_CREATED)
def add_to_queue(body: ProblemIn, db: Session = Depends(get_db), admin: User = Depends(get_admin_user)) -> QueueItemOut:
    item = DailyQueueItem(payload=body.model_dump(), created_by=admin.id)
    db.add(item)
    db.commit()
    return QueueItemOut(id=item.id, title=body.title, difficulty=body.difficulty, created_at=item.created_at, used_on=None)


@router.delete("/admin/daily/queue/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_queue(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)) -> None:
    item = db.get(DailyQueueItem, item_id)
    if item is None or item.used_on is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue item not found or already published")
    db.delete(item)
    db.commit()


@router.post("/admin/daily/prepare", response_model=DailyTodayOut)
async def prepare_now(db: Session = Depends(get_db), admin: User = Depends(get_admin_user)) -> DailyTodayOut:
    """Prepare today's (and tomorrow's) problem right now instead of waiting for the scheduler."""
    await daily.ensure_upcoming()
    return await daily_today(db, admin)
