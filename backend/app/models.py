from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint, false, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetimes on every database (SQLite drops tzinfo on read)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # stored lowercased
    # True once someone has proven they own the email (today: by signing in with Google).
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Null for accounts that only sign in with Google.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Bumped to invalidate every existing session (password change, "log out everywhere").
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Daily-challenge streak, updated when the user solves a daily problem on its day.
    current_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    best_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_daily_solved: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The user's own Gemini API key, encrypted (see crypto.py), plus its last 4 characters for display.
    gemini_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Streak reminder emails: opt-out, and the day the last one was sent (at most one a day).
    reminder_emails: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_reminded: Mapped[date | None] = mapped_column(Date, nullable=True)


# Usernames are unique regardless of case ("Alice" and "alice" can't both exist).
Index("ix_users_username_lower", func.lower(User.username), unique=True)


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(10), index=True)
    tags: Mapped[str] = mapped_column(String(255), default="")  # comma-separated
    starter_prompt: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Hidden from everyone until this moment (daily problems are prepared a day ahead).
    available_from: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    # The author's private solution. Publishing requires it to pass every test (see verification).
    reference_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Taken down by an admin (e.g. after reports). Hidden from everyone but admins.
    hidden_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    hidden_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Up to 3 hints, gentlest first. Solvers reveal them one at a time.
    hints: Mapped[list | None] = mapped_column(JSON, nullable=True)

    author: Mapped[User | None] = relationship()
    daily: Mapped["DailyChallenge | None"] = relationship(back_populates="problem", uselist=False)
    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by="TestCase.position",
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )

    @property
    def tag_list(self) -> list[str]:
        return [t for t in self.tags.split(",") if t]


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # keep pytest from collecting this class

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    input: Mapped[str] = mapped_column(Text, default="")
    # exact | contains | not_contains | regex | json_schema | llm_judge
    check_type: Mapped[str] = mapped_column(String(20))
    # Expected text, regex pattern, JSON schema, or judge rubric depending on check_type.
    expected: Mapped[str] = mapped_column(Text)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    problem: Mapped[Problem] = relationship(back_populates="test_cases")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_user_status", "user_id", "status", "problem_id"),
        Index("ix_submissions_problem_status", "problem_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # accepted | wrong_answer | error
    passed: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    results: Mapped[list] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # The author chose to show this accepted prompt in the problem's Solutions tab.
    shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), index=True)

    user: Mapped[User] = relationship()
    problem: Mapped[Problem] = relationship(back_populates="submissions")


class DailyChallenge(Base):
    """One problem per UTC day."""

    __tablename__ = "daily_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), unique=True)
    source: Mapped[str] = mapped_column(String(10))  # "queue" | "ai"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    problem: Mapped[Problem] = relationship(back_populates="daily")


class DailySolve(Base):
    """A user solved the daily problem on its own day (what streaks are made of)."""

    __tablename__ = "daily_solves"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_daily_solves_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DailyQueueItem(Base):
    """A hand-written problem waiting to become a future daily (used before AI generation)."""

    __tablename__ = "daily_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)  # a ProblemIn
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    used_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class AuthToken(Base):
    """One-time token emailed to a user (verify email, reset password). Only its hash is stored."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # "verify_email" | "reset_password"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UsageDaily(Base):
    """Gemini tokens spent per UTC day, per kind of work and per user (user_id 0 = system)."""

    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("day", "kind", "user_id", name="uq_usage_daily_day_kind_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # run | submit | daily | other
    user_id: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class ProblemReport(Base):
    """A user flagging a problem as broken or inappropriate (one per user per problem)."""

    __tablename__ = "problem_reports"
    __table_args__ = (UniqueConstraint("problem_id", "user_id", name="uq_problem_reports_problem_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(20))  # wrong_tests | unclear | other
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | dismissed | resolved
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    problem: Mapped[Problem] = relationship()
    user: Mapped[User] = relationship()


class HintOpen(Base):
    """How many of a problem's hints a user has revealed."""

    __tablename__ = "hint_opens"
    __table_args__ = (UniqueConstraint("user_id", "problem_id", name="uq_hint_opens_user_problem"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"))
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SolutionVote(Base):
    """An upvote on a shared solution (a Submission with shared=True)."""

    __tablename__ = "solution_votes"
    __table_args__ = (UniqueConstraint("submission_id", "user_id", name="uq_solution_votes_submission_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProblemRating(Base):
    """A solver's opinion of a problem: liked or not, and whether its difficulty label fits."""

    __tablename__ = "problem_ratings"
    __table_args__ = (UniqueConstraint("problem_id", "user_id", name="uq_problem_ratings_problem_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    liked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)  # easier | right | harder
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserBadge(Base):
    """An achievement a user has earned (see badges.py). Kept once earned."""

    __tablename__ = "user_badges"
    __table_args__ = (UniqueConstraint("user_id", "badge", name="uq_user_badges_user_badge"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    badge: Mapped[str] = mapped_column(String(30))
    earned_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModelUsageDaily(Base):
    """Requests per UTC day and model: what each free-tier quota has been spent on."""

    __tablename__ = "model_usage_daily"
    __table_args__ = (UniqueConstraint("day", "model", name="uq_model_usage_daily_day_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    model: Mapped[str] = mapped_column(String(100))  # "provider:model"
    requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Answers served from the cache instead of calling the model.
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    # Rate-limit / quota / server errors from the provider.
    failures: Mapped[int] = mapped_column(Integer, default=0)


class LLMCache(Base):
    """Stored model answers keyed by a hash of (model, request), so repeats cost no quota."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # run | judge
    model: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
