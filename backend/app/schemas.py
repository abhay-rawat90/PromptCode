import json
from datetime import date, datetime
from typing import Literal

import jsonschema
import regex
from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator, model_validator

from .config import settings
from .validation import PASSWORD_MAX, password_error_message, password_problems, username_problems

CheckType = Literal["exact", "contains", "not_contains", "regex", "json_schema", "llm_judge"]
Difficulty = Literal["easy", "medium", "hard"]


# ---------- auth ----------

def _valid_username(v: str) -> str:
    v = v.strip()
    if problems := username_problems(v):
        raise ValueError("Username " + "; ".join(problems) + ".")
    return v


class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    password: str

    _username = field_validator("username")(_valid_username)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str, info: ValidationInfo) -> str:
        if problems := password_problems(v, info.data.get("username", ""), info.data.get("email", "")):
            raise ValueError(password_error_message(problems))
        return v


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=254, description="Username or email")
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class GoogleIn(BaseModel):
    credential: str = Field(min_length=10, max_length=4096)


class UsernameChangeIn(BaseModel):
    username: str

    _username = field_validator("username")(_valid_username)


class PasswordChangeIn(BaseModel):
    current_password: str | None = Field(default=None, max_length=PASSWORD_MAX)
    new_password: str = Field(max_length=PASSWORD_MAX)


class ForgotPasswordIn(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(max_length=PASSWORD_MAX)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class UsernameAvailability(BaseModel):
    available: bool
    reason: str | None = None


class AuthConfig(BaseModel):
    google_client_id: str | None
    daily_test_budget: int


class MeOut(BaseModel):
    id: int
    username: str
    email: str
    avatar_url: str | None
    has_password: bool
    has_google: bool
    email_verified: bool = False
    created_at: datetime
    is_admin: bool = False
    current_streak: int = 0
    best_streak: int = 0
    # One-off message for the UI (e.g. "we removed an unverified password").
    notice: str | None = None

    @classmethod
    def of(cls, user, notice: str | None = None) -> "MeOut":
        from .daily import effective_streak

        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            avatar_url=user.avatar_url,
            has_password=user.password_hash is not None,
            has_google=user.google_sub is not None,
            email_verified=user.email_verified,
            created_at=user.created_at,
            is_admin=user.is_admin,
            current_streak=effective_streak(user),
            best_streak=user.best_streak,
            notice=notice,
        )


class SolvedItem(BaseModel):
    slug: str
    title: str
    difficulty: str


class ProfileOut(BaseModel):
    username: str
    avatar_url: str | None
    created_at: datetime
    solved: int
    solved_by_difficulty: dict[str, int]
    total_by_difficulty: dict[str, int]
    submissions: int
    acceptance_rate: float | None
    problems_authored: int
    recent_solved: list[SolvedItem]
    current_streak: int
    best_streak: int
    dailies_solved: int


# ---------- problems ----------

class TestCaseIn(BaseModel):
    __test__ = False

    input: str = Field(default="", max_length=20_000)
    check_type: CheckType
    expected: str = Field(min_length=1, max_length=20_000)
    case_sensitive: bool = False
    is_sample: bool = False

    @model_validator(mode="after")
    def validate_expected(self) -> "TestCaseIn":
        if self.check_type == "regex":
            try:
                regex.compile(self.expected)
            except regex.error as exc:
                raise ValueError(f"Invalid regex: {exc}") from exc
        elif self.check_type == "json_schema":
            try:
                schema = json.loads(self.expected)
                jsonschema.Draft202012Validator.check_schema(schema)
            except (json.JSONDecodeError, jsonschema.SchemaError) as exc:
                raise ValueError(f"Invalid JSON schema: {exc}") from exc
        return self


class TestCaseOut(TestCaseIn):
    id: int
    position: int


class ProblemIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=1, max_length=20_000)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list, max_length=8)
    starter_prompt: str = Field(default="", max_length=settings.max_prompt_chars)
    test_cases: list[TestCaseIn] = Field(min_length=1, max_length=settings.max_tests_per_problem)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        cleaned = []
        for tag in tags:
            tag = tag.strip().lower().replace(",", " ")[:30]
            if tag and tag not in cleaned:
                cleaned.append(tag)
        return cleaned

    @model_validator(mode="after")
    def needs_sample(self) -> "ProblemIn":
        if not any(t.is_sample for t in self.test_cases):
            raise ValueError("At least one test case must be a visible sample.")
        return self


class ProblemSummary(BaseModel):
    id: int
    slug: str
    title: str
    difficulty: Difficulty
    tags: list[str]
    author: str | None
    solved: bool
    attempted: bool
    submissions: int
    acceptance: float | None


class ProblemDetail(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    difficulty: Difficulty
    tags: list[str]
    starter_prompt: str
    author: str | None
    sample_tests: list[TestCaseOut]
    hidden_count: int
    can_edit: bool
    solved: bool
    daily_day: date | None = None


class ProblemFull(ProblemIn):
    """Everything an author needs to edit their problem, including hidden tests."""
    slug: str


# ---------- running / submissions ----------

class RunIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=settings.max_prompt_chars)


class TestResultOut(BaseModel):
    test_id: int
    position: int
    is_sample: bool
    check_type: CheckType
    passed: bool
    error: bool
    input: str | None
    expected: str | None
    output: str | None
    detail: str | None


class RunOut(BaseModel):
    status: Literal["accepted", "wrong_answer", "error"]
    passed: int
    total: int
    results: list[TestResultOut]
    input_tokens: int
    output_tokens: int
    submission_id: int | None = None
    daily_remaining: int | None = None
    # Set when this submission solved today's daily problem for the first time.
    streak_extended: bool = False
    current_streak: int | None = None


class SubmissionOut(BaseModel):
    id: int
    problem_slug: str
    problem_title: str
    status: str
    passed: int
    total: int
    input_tokens: int
    output_tokens: int
    created_at: datetime


class SubmissionDetail(SubmissionOut):
    prompt: str
    results: list[TestResultOut]


class DailyProblemOut(BaseModel):
    slug: str
    title: str
    difficulty: Difficulty
    tags: list[str]


class DailyTodayOut(BaseModel):
    status: Literal["ready", "preparing", "unavailable", "disabled"]
    day: date
    next_reset: datetime
    problem: DailyProblemOut | None = None
    solved_today: bool = False
    current_streak: int = 0
    best_streak: int = 0


class DailyArchiveItem(BaseModel):
    day: date
    problem: DailyProblemOut
    solved_on_day: bool
    solved: bool


class QueueItemOut(BaseModel):
    id: int
    title: str
    difficulty: Difficulty
    created_at: datetime
    used_on: date | None


class LeaderboardEntry(BaseModel):
    username: str
    solved: int
    submissions: int
