import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-please"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # "development" or "production". Production refuses to start with unsafe defaults.
    environment: str = os.getenv("ENVIRONMENT", "development").lower()

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./promptcode.db")
    # Run Alembic migrations on startup. Turn off when several instances share one database
    # and run `alembic upgrade head` as a deploy step instead.
    auto_migrate: bool = _bool("AUTO_MIGRATE", True)

    # ---- auth ----
    jwt_secret: str = os.getenv("JWT_SECRET", DEV_JWT_SECRET)
    session_hours: int = int(os.getenv("SESSION_HOURS", "168"))
    cookie_secure: bool = _bool("COOKIE_SECURE", False)  # True when served over HTTPS
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")

    # ---- web ----
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173"))
    # Trust X-Forwarded-For (only when running behind a reverse proxy you control).
    trust_proxy: bool = _bool("TRUST_PROXY", False)
    rate_limit_storage: str = os.getenv("RATE_LIMIT_STORAGE", "memory://")

    # ---- LLM ----
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    judge_model: str = os.getenv("JUDGE_MODEL", "gemini-3.6-flash")
    # Thinking level: minimal | low | medium | high, or empty to use the model default.
    run_thinking: str = os.getenv("RUN_THINKING", "low")
    judge_thinking: str = os.getenv("JUDGE_THINKING", "low")
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))
    # Parallel test cases per request, and across the whole process.
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "3"))
    global_llm_concurrency: int = int(os.getenv("GLOBAL_LLM_CONCURRENCY", "20"))
    # Test-case executions each user may spend per day (protects your API quota).
    daily_test_budget: int = int(os.getenv("DAILY_TEST_BUDGET", "300"))

    # ---- daily challenge ----
    daily_enabled: bool = _bool("DAILY_ENABLED", True)
    # Run the background job that prepares today's and tomorrow's problem. With several
    # instances, enable it on one (or run `python -m app.daily` from cron instead).
    daily_scheduler: bool = _bool("DAILY_SCHEDULER", True)
    daily_model: str = os.getenv("DAILY_MODEL", "gemini-3.6-flash")
    daily_generation_attempts: int = int(os.getenv("DAILY_GENERATION_ATTEMPTS", "3"))

    # Hard limits that keep a single run cheap.
    max_prompt_chars: int = 8000
    max_tests_per_problem: int = 20

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if not self.is_production:
            return
        problems = []
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a random string of at least 32 characters")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true (serve over HTTPS)")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


settings = Settings()
