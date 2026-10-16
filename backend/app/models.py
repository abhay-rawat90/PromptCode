from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint, false, func
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
