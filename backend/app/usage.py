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
from .models import UsageDaily

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


def record(input_tokens: int, output_tokens: int) -> None:
    """Add one request's tokens to today's counters. Never raises."""
    kind, user_id = _attribution.get()
    values = {"day": _today(), "kind": kind, "user_id": user_id, "requests": 1,
              "input_tokens": input_tokens, "output_tokens": output_tokens}
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(UsageDaily).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "kind", "user_id"],
        set_={
            "requests": UsageDaily.requests + 1,
            "input_tokens": UsageDaily.input_tokens + input_tokens,
            "output_tokens": UsageDaily.output_tokens + output_tokens,
        },
    )
    try:
        with SessionLocal() as db:
            db.execute(stmt)
            db.commit()
    except Exception:  # noqa: BLE001 - accounting must never break grading
        log.exception("Could not record usage")


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
