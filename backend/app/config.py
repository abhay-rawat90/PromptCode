import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-please"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _database_url(url: str) -> str:
    # Hosts like Render hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _list(name: str, default: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    # "development" or "production". Production refuses to start with unsafe defaults.
    environment: str = os.getenv("ENVIRONMENT", "development").lower()

    database_url: str = field(default_factory=lambda: _database_url(os.getenv("DATABASE_URL", "sqlite:///./promptcode.db")))
    # Run Alembic migrations on startup. Turn off when several instances share one database
    # and run `alembic upgrade head` as a deploy step instead.
    auto_migrate: bool = _bool("AUTO_MIGRATE", True)

    # ---- auth ----
    jwt_secret: str = os.getenv("JWT_SECRET", DEV_JWT_SECRET)
    session_hours: int = int(os.getenv("SESSION_HOURS", "168"))
    cookie_secure: bool = _bool("COOKIE_SECURE", False)  # True when served over HTTPS
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")

    # ---- web ----
    # Public URL of the site, used in email links.
    app_url: str = os.getenv("APP_URL", "http://localhost:5173").rstrip("/")
    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", os.getenv("APP_URL", "http://localhost:5173").rstrip("/"))
    )
    # Trust X-Forwarded-For (only when running behind a reverse proxy you control). The client IP
    # is taken this many entries from the right, i.e. the address your own proxy saw.
    trust_proxy: bool = _bool("TRUST_PROXY", False)
    trusted_proxy_hops: int = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")

    # ---- email ----
    # "resend" sends through Resend's API; "console" logs emails (local development).
    email_backend: str = os.getenv("EMAIL_BACKEND", "resend" if os.getenv("RESEND_API_KEY") else "console")
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    email_from: str = os.getenv("EMAIL_FROM", "PromptCode <onboarding@resend.dev>")
    # Publishing problems requires a verified email (blocks throwaway spam accounts).
    require_verified_to_publish: bool = _bool("REQUIRE_VERIFIED_TO_PUBLISH", True)
    # Let every user create problems. Off: only admins can, and problems made by non-admins
    # are hidden (kept in the database, so turning this back on restores them).
    allow_user_problems: bool = _bool("ALLOW_USER_PROBLEMS", False)
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
    # Site-wide cap on Gemini tokens per UTC day (0 = no cap). Runs and submits stop when reached.
    daily_token_cap: int = int(os.getenv("DAILY_TOKEN_CAP", "0"))

    # ---- quota handling (free tiers) ----
    # Groq (free key at console.groq.com) supplies backup models when Gemini is at its limit.
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    # Backup models tried in order when the main model is out of quota or too busy.
    # Format: "provider:model" (provider is gemini or groq). Empty = no backups.
    run_fallback_models: list[str] = field(
        default_factory=lambda: _list("RUN_FALLBACK_MODELS", "groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b")
    )
    judge_fallback_models: list[str] = field(
        default_factory=lambda: _list(
            "JUDGE_FALLBACK_MODELS",
            "groq:openai/gpt-oss-120b,gemini:" + os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        )
    )
    daily_fallback_models: list[str] = field(
        default_factory=lambda: _list("DAILY_FALLBACK_MODELS", "groq:openai/gpt-oss-120b")
    )
    # Per-model pacing, overriding the built-in defaults: "model=rpm:tpm:rpd,..." (0 = no limit).
    # Check your real free-tier numbers at https://aistudio.google.com/rate-limit and console.groq.com.
    model_limits: str = os.getenv("MODEL_LIMITS", "")
    # How long a request waits for the main model's per-minute capacity before using a backup,
    # and how long a user's request waits for any model at all.
    fallback_wait_seconds: float = float(os.getenv("FALLBACK_WAIT_SECONDS", "10"))
    user_wait_seconds: float = float(os.getenv("USER_WAIT_SECONDS", "45"))
    # Identical requests (same model, prompt, input) reuse a stored answer for this many days.
    llm_cache_days: int = int(os.getenv("LLM_CACHE_DAYS", "30"))
    # Sampling temperature for solver prompts; empty = the model's default.
    run_temperature: str = os.getenv("RUN_TEMPERATURE", "")
    # Users may add their own Gemini API key (their runs then don't touch the site's quota).
    allow_user_keys: bool = _bool("ALLOW_USER_KEYS", True)
    # Encrypts stored user API keys. Defaults to a key derived from JWT_SECRET; set it separately
    # so rotating JWT_SECRET doesn't make stored keys unreadable.
    key_encryption_secret: str = os.getenv("KEY_ENCRYPTION_SECRET", "")

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
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point at Postgres (SQLite data is lost on redeploy)")
        if not self.app_url.startswith("https://"):
            problems.append("APP_URL must be the site's https:// address (used in email links)")
        if self.email_backend == "resend" and not self.resend_api_key:
            problems.append("RESEND_API_KEY is required to send emails")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


settings = Settings()
