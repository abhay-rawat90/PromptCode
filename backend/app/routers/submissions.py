from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..security import get_current_user
from ..db import get_db
from ..models import Problem, Submission, User
from ..schemas import LeaderboardEntry, SubmissionDetail, SubmissionOut

router = APIRouter(prefix="/api", tags=["submissions"])


def _summary(s: Submission) -> dict:
    return {
        "id": s.id,
        "problem_slug": s.problem.slug,
        "problem_title": s.problem.title,
        "status": s.status,
        "passed": s.passed,
        "total": s.total,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "created_at": s.created_at,
    }


@router.get("/submissions", response_model=list[SubmissionOut])
def my_submissions(
    problem: str | None = Query(None, description="Filter by problem slug"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    stmt = (
        select(Submission)
        .where(Submission.user_id == user.id)
        .options(selectinload(Submission.problem))
        .order_by(Submission.id.desc())
        .limit(limit)
    )
    if problem:
        stmt = stmt.join(Problem).where(Problem.slug == problem)
    return [_summary(s) for s in db.scalars(stmt)]


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def submission_detail(
    submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    s = db.get(Submission, submission_id)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return {**_summary(s), "prompt": s.prompt, "results": s.results}


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
def leaderboard(db: Session = Depends(get_db)) -> list[LeaderboardEntry]:
    solved = (
        select(Submission.user_id, func.count(func.distinct(Submission.problem_id)).label("solved"))
        .where(Submission.status == "accepted")
        .group_by(Submission.user_id)
        .subquery()
    )
    totals = (
        select(Submission.user_id, func.count().label("submissions"))
        .group_by(Submission.user_id)
        .subquery()
    )
    rows = db.execute(
        select(User.username, solved.c.solved, totals.c.submissions)
        .join(solved, solved.c.user_id == User.id)
        .join(totals, totals.c.user_id == User.id)
        .order_by(solved.c.solved.desc(), totals.c.submissions.asc())
        .limit(50)
    )
    return [LeaderboardEntry(username=u, solved=s, submissions=n) for u, s, n in rows]
