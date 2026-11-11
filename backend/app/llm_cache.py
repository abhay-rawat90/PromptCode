"""Stored model answers, so an identical request (same model, prompt, input, settings) costs no
quota the second time: re-running a prompt, Submit re-running the samples Run already did,
several people trying the same starter prompt, judge verdicts for identical outputs."""

import hashlib
import json
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import SessionLocal
from .models import LLMCache, utcnow

log = logging.getLogger("promptcode.cache")
_PURGE_EVERY = 3600.0
_last_purge = 0.0


def make_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def enabled() -> bool:
    return settings.llm_cache_days > 0


def get(key: str) -> dict | None:
    if not enabled():
        return None
    try:
        with SessionLocal() as db:
            return db.scalar(
                select(LLMCache.payload).where(
                    LLMCache.key == key,
                    LLMCache.created_at >= utcnow() - timedelta(days=settings.llm_cache_days),
                )
            )
    except Exception:  # noqa: BLE001 - a cache problem must never break grading
        log.exception("Cache read failed")
        return None


def put(key: str, kind: str, model: str, payload: dict) -> None:
    if not enabled():
        return
    try:
        with SessionLocal() as db:
            existing = db.get(LLMCache, key)
            if existing:  # an expired entry: refresh it
                existing.payload, existing.created_at = payload, utcnow()
            else:
                db.add(LLMCache(key=key, kind=kind, model=model, payload=payload))
            db.commit()
    except IntegrityError:
        pass  # a concurrent request stored the same answer first
    except Exception:  # noqa: BLE001
        log.exception("Cache write failed")
    _maybe_purge()


def _maybe_purge() -> None:
    global _last_purge
    if time.monotonic() - _last_purge < _PURGE_EVERY:
        return
    _last_purge = time.monotonic()
    try:
        with SessionLocal() as db:
            db.execute(delete(LLMCache).where(LLMCache.created_at < utcnow() - timedelta(days=settings.llm_cache_days)))
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Cache purge failed")
