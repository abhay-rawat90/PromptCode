"""Rate limiting backed by memory (single process) or Redis (RATE_LIMIT_STORAGE=redis://...)."""

import math
import time

from fastapi import HTTPException, status
from limits import RateLimitItem, parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

from .config import settings

_limiter = MovingWindowRateLimiter(storage_from_string(settings.rate_limit_storage))

# Named limits so they're easy to tune in one place.
LIMITS: dict[str, RateLimitItem] = {
    "register_ip": parse("5/hour"),
    "login_ip": parse("20/minute"),
    "login_account": parse("8/15minutes"),
    "google_ip": parse("20/minute"),
    "username_check_ip": parse("60/minute"),
    "account_change": parse("10/hour"),
    "forgot_ip": parse("10/hour"),
    "forgot_email": parse("3/hour"),
    "reset_ip": parse("20/hour"),
    "verify_resend_user": parse("3/hour"),
    "run_user": parse("15/minute"),
    "submit_user": parse("6/minute"),
    "create_problem_user": parse("15/hour"),
    "daily_tests_user": parse(f"{settings.daily_test_budget}/day"),
}


def _retry_after(item: RateLimitItem, *identifiers: str) -> int:
    stats = _limiter.get_window_stats(item, *identifiers)
    return max(1, math.ceil(stats.reset_time - time.time()))


def _human(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{math.ceil(seconds / 60)} minutes"
    return f"{math.ceil(seconds / 3600)} hours"


def enforce(name: str, *identifiers: str, cost: int = 1, message: str | None = None) -> None:
    """Consume `cost` from the named limit for these identifiers, or raise 429."""
    item = LIMITS[name]
    if _limiter.hit(item, name, *identifiers, cost=cost):
        return
    wait = _retry_after(item, name, *identifiers)
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        message or f"Too many attempts. Please try again in {_human(wait)}.",
        headers={"Retry-After": str(wait)},
    )


def remaining(name: str, *identifiers: str) -> int:
    return _limiter.get_window_stats(LIMITS[name], name, *identifiers).remaining


def reset() -> None:
    """Clear all counters (used by tests)."""
    _limiter.storage.reset()
