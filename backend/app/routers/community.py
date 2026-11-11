"""Hints, the Solutions tab (official + shared solutions with upvotes) and problem ratings."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import badges, community, ratelimit
from ..db import get_db
from ..models import HintOpen, ProblemRating, SolutionVote, Submission, User, utcnow
from ..schemas import HintsOut, RatingIn, RatingOut, ShareIn, SolutionOut, SolutionsOut, VoteOut
from ..security import get_current_user
from .problems import _get_problem

router = APIRouter(prefix="/api", tags=["community"])

LOCKED = "Solve this problem to unlock its solutions."


# ---------- hints ----------

@router.post("/problems/{slug}/hints/{number}", response_model=HintsOut)
def reveal_hint(
    slug: str, number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> HintsOut:
    """Reveal hint `number` (1-based). Hints open in order; opening one again is harmless."""
    problem = _get_problem(db, slug, user)
    hints = problem.hints or []
    if not 1 <= number <= len(hints):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This problem has no such hint.")
    ratelimit.enforce("hint_user", str(user.id))
    row = db.scalar(select(HintOpen).where(HintOpen.user_id == user.id, HintOpen.problem_id == problem.id))
    opened = row.count if row else 0
    if number > opened + 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Open the earlier hints first.")
    if number > opened:
        if row is None:
            row = HintOpen(user_id=user.id, problem_id=problem.id, count=0)
            db.add(row)
        row.count = number
        try:
            db.commit()
        except IntegrityError:  # two clicks at once
            db.rollback()
        opened = max(opened, number)
    return HintsOut(hint_count=len(hints), hints=hints[:opened])


# ---------- solutions ----------

def _tokens():
    return Submission.input_tokens + Submission.output_tokens


def _can_view_solutions(db: Session, user: User, problem_id: int) -> bool:
    return user.is_admin or community.has_solved(db, user, problem_id)


@router.get("/problems/{slug}/solutions", response_model=SolutionsOut)
def solutions(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> SolutionsOut:
    """The official solution and the prompts other solvers chose to share. Locked until solved."""
    problem = _get_problem(db, slug, user)
    if not _can_view_solutions(db, user, problem.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, LOCKED)

    votes = (
        select(SolutionVote.submission_id, func.count().label("votes"))
        .group_by(SolutionVote.submission_id)
        .subquery()
    )
    vote_count = func.coalesce(votes.c.votes, 0)
    rows = db.execute(
        select(Submission, vote_count)
        .outerjoin(votes, votes.c.submission_id == Submission.id)
        .where(Submission.problem_id == problem.id, Submission.shared.is_(True), Submission.status == "accepted")
        .options(selectinload(Submission.user))
        .order_by(_tokens(), vote_count.desc(), Submission.id)
        .limit(100)
    ).all()
    mine_voted = set(
        db.scalars(
            select(SolutionVote.submission_id).where(
                SolutionVote.user_id == user.id, SolutionVote.submission_id.in_([s.id for s, _ in rows])
            )
        )
    )
    my_accepted = (Submission.user_id == user.id, Submission.problem_id == problem.id, Submission.status == "accepted")
    return SolutionsOut(
        official=problem.reference_prompt or None,
        community=[
            SolutionOut(
                id=s.id,
                username=s.user.username,
                avatar_url=s.user.avatar_url,
                prompt=s.prompt,
                tokens=s.input_tokens + s.output_tokens,
                votes=count,
                voted=s.id in mine_voted,
                mine=s.user_id == user.id,
                created_at=s.created_at,
            )
            for s, count in rows
        ],
        my_best_id=db.scalar(select(Submission.id).where(*my_accepted).order_by(_tokens(), Submission.id.desc()).limit(1)),
        my_shared_id=db.scalar(select(Submission.id).where(*my_accepted, Submission.shared.is_(True)).limit(1)),
    )


@router.post("/submissions/{submission_id}/share", response_model=ShareIn)
def share_submission(
    submission_id: int, body: ShareIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ShareIn:
    """Show (or stop showing) one of your accepted prompts in the problem's Solutions tab.
    You share one solution per problem: sharing another replaces it."""
    submission = db.get(Submission, submission_id)
    if submission is None or submission.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if submission.status != "accepted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only accepted submissions can be shared.")
    if body.shared:
        if not user.email_verified:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify your email to share solutions.")
        ratelimit.enforce("share_user", str(user.id))
        db.execute(
            update(Submission)
            .where(Submission.user_id == user.id, Submission.problem_id == submission.problem_id, Submission.id != submission.id)
            .values(shared=False)
        )
    submission.shared = body.shared
    db.commit()
    return ShareIn(shared=submission.shared)


@router.post("/solutions/{submission_id}/vote", response_model=VoteOut)
def vote_solution(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> VoteOut:
    """Toggle your upvote on a shared solution."""
    solution = db.get(Submission, submission_id)
    if solution is None or not solution.shared:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solution not found")
    if solution.user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't upvote your own solution.")
    if not _can_view_solutions(db, user, solution.problem_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, LOCKED)
    ratelimit.enforce("vote_user", str(user.id))

    existing = db.scalar(
        select(SolutionVote).where(SolutionVote.submission_id == submission_id, SolutionVote.user_id == user.id)
    )
    if existing:
        db.delete(existing)
    else:
        db.add(SolutionVote(submission_id=submission_id, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    count = db.scalar(select(func.count()).select_from(SolutionVote).where(SolutionVote.submission_id == submission_id)) or 0
    if not existing and count >= badges.HELPER_VOTES and (author := db.get(User, solution.user_id)):
        badges.evaluate(db, author)  # the author may have just earned "Helper"
    return VoteOut(votes=count, voted=not existing)


# ---------- ratings ----------

@router.put("/problems/{slug}/rating", response_model=RatingOut)
def rate_problem(
    slug: str, body: RatingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> RatingOut:
    """Like or dislike a problem and say whether its difficulty label fits. Solvers only."""
    problem = _get_problem(db, slug, user)
    if not community.has_solved(db, user, problem.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solve this problem to rate it.")
    ratelimit.enforce("rate_user", str(user.id))
    rating = db.scalar(select(ProblemRating).where(ProblemRating.problem_id == problem.id, ProblemRating.user_id == user.id))
    if rating is None:
        rating = ProblemRating(problem_id=problem.id, user_id=user.id)
        db.add(rating)
    rating.liked, rating.difficulty, rating.updated_at = body.liked, body.difficulty, utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return RatingOut(**community.rating_summary(db, problem.id, user))
