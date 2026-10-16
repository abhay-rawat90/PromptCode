from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from .. import daily
from ..models import DailySolve, Problem, Submission, User
from ..schemas import ProfileOut, SolvedItem

router = APIRouter(prefix="/api/users", tags=["users"])

DIFFICULTIES = ("easy", "medium", "hard")


@router.get("/{username}", response_model=ProfileOut)
def profile(username: str, db: Session = Depends(get_db)) -> ProfileOut:
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # First accepted submission per problem, newest first.
    first_solve = (
        select(Submission.problem_id, func.min(Submission.created_at).label("solved_at"))
        .where(Submission.user_id == user.id, Submission.status == "accepted")
        .group_by(Submission.problem_id)
        .subquery()
    )
    solved_rows = db.execute(
        select(Problem.slug, Problem.title, Problem.difficulty)
        .join(first_solve, first_solve.c.problem_id == Problem.id)
        .order_by(first_solve.c.solved_at.desc())
    ).all()

    total_by_difficulty = dict.fromkeys(DIFFICULTIES, 0)
    visible = select(Problem.difficulty, func.count()).where(daily.visible_problem_clause()).group_by(Problem.difficulty)
    for difficulty, count in db.execute(visible):
        total_by_difficulty[difficulty] = count
    solved_by_difficulty = dict.fromkeys(DIFFICULTIES, 0)
    for row in solved_rows:
        solved_by_difficulty[row.difficulty] += 1

    submissions, accepted = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((Submission.status == "accepted", 1), else_=0)), 0),
        ).where(Submission.user_id == user.id)
    ).one()

    return ProfileOut(
        username=user.username,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        solved=len(solved_rows),
        solved_by_difficulty=solved_by_difficulty,
        total_by_difficulty=total_by_difficulty,
        submissions=submissions,
        acceptance_rate=round(100 * accepted / submissions, 1) if submissions else None,
        problems_authored=db.scalar(select(func.count()).where(Problem.author_id == user.id)) or 0,
        recent_solved=[SolvedItem(slug=r.slug, title=r.title, difficulty=r.difficulty) for r in solved_rows[:10]],
        current_streak=daily.effective_streak(user),
        best_streak=user.best_streak,
        dailies_solved=db.scalar(select(func.count()).where(DailySolve.user_id == user.id)) or 0,
    )

