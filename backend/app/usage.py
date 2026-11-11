"""Track Gemini token spend per day, kind of work and user, and enforce a site-wide daily cap.

Every Gemini call reports its tokens here (see llm._generate). Callers say what the work is for
with `attribute(kind, user_id)`; asyncio tasks inherit it, so the judge calls made while grading a
submission are counted as that user's "submit" usage.
"""

import contextlib
import contextvars
import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .config import settings
from .db import SessionLocal, engine
from .models import ModelUsageDaily, UsageDaily

log = logging.getLogger("promptcode.usage")
_attribution = contextvars.ContextVar("usage_attribution", default=("other", 0))


@contextlib.contextmanager
def attribute(kind: str, user_id: int | None = None):
    token = _attribution.set((kind, user_id or 0))
    try:
        yield
    finally:
        _attribution.reset(token)


def _today():
    from .daily import today  # the daily module owns "what day is it" (and tests control it)

    return today()


def _upsert(table, keys: dict, counters: dict) -> None:
    """Insert a row of counters, or add them to the existing row. Never raises."""
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(table).values(**keys, **counters)
    stmt = stmt.on_conflict_do_update(
        index_elements=list(keys),
        set_={name: getattr(table, name) + value for name, value in counters.items()},
    )
    try:
        with SessionLocal() as db:
            db.execute(stmt)
            db.commit()
    except Exception:  # noqa: BLE001 - accounting must never break grading
        log.exception("Could not record usage")


def record(input_tokens: int, output_tokens: int) -> None:
    """Add one request's tokens to today's counters (per kind of work and user)."""
    kind, user_id = _attribution.get()
    _upsert(UsageDaily, {"day": _today(), "kind": kind, "user_id": user_id},
            {"requests": 1, "input_tokens": input_tokens, "output_tokens": output_tokens})


def record_model(model: str, *, input_tokens: int = 0, output_tokens: int = 0, requests: int = 0,
                 cache_hits: int = 0, failures: int = 0) -> None:
    """Per-model counters: requests spent on each free-tier quota, cache savings, errors."""
    _upsert(ModelUsageDaily, {"day": _today(), "model": model},
            {"requests": requests, "input_tokens": input_tokens, "output_tokens": output_tokens,
             "cache_hits": cache_hits, "failures": failures})


def tokens_today() -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.coalesce(func.sum(UsageDaily.input_tokens + UsageDaily.output_tokens), 0)).where(
                UsageDaily.day == _today()
            )
        )


def enforce_daily_cap() -> None:
    if settings.daily_token_cap and tokens_today() >= settings.daily_token_cap:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PromptCode has used today's AI budget. Running prompts resumes after midnight UTC.",
        )
