"""Shared lookups for the community features around a problem: hints a user has revealed,
ratings, and its neighbours in the problem list."""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import daily
from .models import HintOpen, Problem, ProblemRating, Submission, User


def has_solved(db: Session, user: User | None, problem_id: int) -> bool:
    if user is None:
        return False
    return db.scalar(
        select(Submission.id)
        .where(Submission.user_id == user.id, Submission.problem_id == problem_id, Submission.status == "accepted")
        .limit(1)
    ) is not None


def hints_opened(db: Session, user: User | None, problem_id: int) -> int:
    if user is None:
        return 0
    return db.scalar(
        select(HintOpen.count).where(HintOpen.user_id == user.id, HintOpen.problem_id == problem_id)
    ) or 0


def opened_hints(db: Session, user: User | None, problem: Problem) -> list[str]:
    """The hints this user has revealed so far (hints are opened in order)."""
    return list((problem.hints or [])[: hints_opened(db, user, problem.id)])


def rating_summary(db: Session, problem_id: int, user: User | None) -> dict:
    likes, dislikes = db.execute(
        select(
            func.coalesce(func.sum(case((ProblemRating.liked.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((ProblemRating.liked.is_(False), 1), else_=0)), 0),
        ).where(ProblemRating.problem_id == problem_id)
    ).one()
    mine = None
    if user is not None:
        row = db.scalar(select(ProblemRating).where(ProblemRating.problem_id == problem_id, ProblemRating.user_id == user.id))
        if row is not None:
            mine = {"liked": row.liked, "difficulty": row.difficulty}
    return {"likes": likes, "dislikes": dislikes, "my_rating": mine}


def like_stats(db: Session) -> dict[int, tuple[int, int]]:
    """problem id -> (likes, likes + dislikes), for problems with at least one like or dislike."""
    rows = db.execute(
        select(
            ProblemRating.problem_id,
            func.sum(case((ProblemRating.liked.is_(True), 1), else_=0)),
            func.count(),
        )
        .where(ProblemRating.liked.is_not(None))
        .group_by(ProblemRating.problem_id)
    )
    return {pid: (likes, total) for pid, likes, total in rows}


def neighbours(db: Session, problem: Problem) -> dict:
    """Slugs of the previous and next visible problems, in list order."""
    visible = daily.visible_problem_clause()
    return {
        "prev_slug": db.scalar(
            select(Problem.slug).where(visible, Problem.id < problem.id).order_by(Problem.id.desc()).limit(1)
        ),
        "next_slug": db.scalar(
            select(Problem.slug).where(visible, Problem.id > problem.id).order_by(Problem.id).limit(1)
        ),
    }
