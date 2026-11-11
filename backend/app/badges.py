"""Achievements. Every badge is computed from data the site already has, so existing users get
the ones they've earned the first time they're evaluated. A badge is kept once earned."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import daily
from .models import HintOpen, Problem, SolutionVote, Submission, User, UserBadge

HELPER_VOTES = 5


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    icon: str
    description: str


BADGES = [
    Badge("first_solve", "First Solve", "🎯", "Solve your first problem."),
    Badge("solved_10", "Getting Fluent", "📘", "Solve 10 problems."),
    Badge("solved_50", "Prompt Smith", "⚒️", "Solve 50 problems."),
    Badge("solved_100", "Centurion", "💯", "Solve 100 problems."),
    Badge("streak_3", "Warming Up", "🔥", "Reach a 3-day daily streak."),
    Badge("streak_7", "On Fire", "🚀", "Reach a 7-day daily streak."),
    Badge("streak_30", "Unstoppable", "🌋", "Reach a 30-day daily streak."),
    Badge("all_easy", "Clean Sweep", "🧹", "Solve every Easy problem."),
    Badge("first_hard", "Hard Hitter", "🥊", "Solve a Hard problem."),
    Badge("one_shot", "One-Shot", "⚡", "Get a problem accepted on your first submission, without hints."),
    Badge("minimalist", "Minimalist", "✂️", "Hold the lowest token count on a problem that someone else has solved too."),
    Badge("helper", "Helper", "🤝", f"Share a solution that gets {HELPER_VOTES} upvotes."),
]
BY_KEY = {b.key: b for b in BADGES}


def _earned_now(db: Session, user: User) -> set[str]:
    """Keys of every badge the user's current data qualifies for."""
    earned: set[str] = set()
    visible = daily.visible_problem_clause()

    solved = db.execute(
        select(Problem.id, Problem.difficulty)
        .join(Submission, Submission.problem_id == Problem.id)
        .where(Submission.user_id == user.id, Submission.status == "accepted", visible)
        .distinct()
    ).all()
    solved_ids = {pid for pid, _ in solved}
    for threshold, key in ((1, "first_solve"), (10, "solved_10"), (50, "solved_50"), (100, "solved_100")):
        if len(solved_ids) >= threshold:
            earned.add(key)
    for threshold, key in ((3, "streak_3"), (7, "streak_7"), (30, "streak_30")):
        if user.best_streak >= threshold:
            earned.add(key)
    if any(difficulty == "hard" for _, difficulty in solved):
        earned.add("first_hard")
    easy_ids = set(db.scalars(select(Problem.id).where(visible, Problem.difficulty == "easy")))
    if easy_ids and easy_ids <= solved_ids:
        earned.add("all_easy")
    if not solved_ids:
        return earned

    # One-shot: the very first submission to a problem was accepted, and no hint was opened.
    first = (
        select(func.min(Submission.id).label("id"))
        .where(Submission.user_id == user.id)
        .group_by(Submission.problem_id)
        .subquery()
    )
    hinted = select(HintOpen.problem_id).where(HintOpen.user_id == user.id, HintOpen.count > 0)
    if db.scalar(
        select(Submission.id)
        .join(first, first.c.id == Submission.id)
        .where(Submission.status == "accepted", Submission.problem_id.not_in(hinted))
        .limit(1)
    ):
        earned.add("one_shot")

    # Minimalist: beat every other solver's token count on some problem.
    tokens = Submission.input_tokens + Submission.output_tokens
    mine = dict(db.execute(
        select(Submission.problem_id, func.min(tokens))
        .where(Submission.user_id == user.id, Submission.status == "accepted")
        .group_by(Submission.problem_id)
    ).all())
    others = dict(db.execute(
        select(Submission.problem_id, func.min(tokens))
        .where(Submission.user_id != user.id, Submission.status == "accepted", Submission.problem_id.in_(mine))
        .group_by(Submission.problem_id)
    ).all())
    if any(pid in others and best < others[pid] for pid, best in mine.items()):
        earned.add("minimalist")

    # Helper: a shared solution with enough upvotes.
    if db.scalar(
        select(SolutionVote.submission_id)
        .join(Submission, Submission.id == SolutionVote.submission_id)
        .where(Submission.user_id == user.id, Submission.shared.is_(True))
        .group_by(SolutionVote.submission_id)
        .having(func.count() >= HELPER_VOTES)
        .limit(1)
    ):
        earned.add("helper")
    return earned


def evaluate(db: Session, user: User) -> list[dict]:
    """Award any badges the user has newly earned. Returns them (for a toast). Commits."""
    have = set(db.scalars(select(UserBadge.badge).where(UserBadge.user_id == user.id)))
    new = [key for key in _earned_now(db, user) if key not in have]
    if not new:
        return []
    db.add_all(UserBadge(user_id=user.id, badge=key) for key in new)
    try:
        db.commit()
    except IntegrityError:  # a concurrent request awarded the same badge
        db.rollback()
        return []
    return [_out(BY_KEY[key]) for key in sorted(new, key=lambda k: BADGES.index(BY_KEY[k]))]


def for_profile(db: Session, user: User) -> list[dict]:
    """Every badge in display order, with when this user earned it (None = not yet)."""
    evaluate(db, user)  # backfill: users who qualified before badges existed
    earned = dict(db.execute(select(UserBadge.badge, UserBadge.earned_at).where(UserBadge.user_id == user.id)).all())
    return [_out(b, earned.get(b.key)) for b in BADGES]


def _out(badge: Badge, earned_at=None) -> dict:
    return {"key": badge.key, "name": badge.name, "icon": badge.icon, "description": badge.description,
            "earned_at": earned_at}
