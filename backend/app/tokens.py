"""Single-use tokens sent by email. The raw token only ever exists in the email link."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import AuthToken, User, utcnow

LIFETIMES = {"verify_email": timedelta(hours=48), "reset_password": timedelta(hours=1)}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(db: Session, user: User, purpose: str) -> str:
    """Create a token, invalidating earlier unused ones for the same purpose. Commits."""
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=_hash(raw), expires_at=utcnow() + LIFETIMES[purpose]))
    db.commit()
    return raw


def consume(db: Session, raw: str, purpose: str) -> User | None:
    """Return the token's user and mark it used, or None if invalid/expired/used. Doesn't commit."""
    token = db.scalar(select(AuthToken).where(AuthToken.token_hash == _hash(raw), AuthToken.purpose == purpose))
    if token is None or token.used_at is not None or token.expires_at < utcnow():
        return None
    token.used_at = utcnow()
    return db.get(User, token.user_id)
