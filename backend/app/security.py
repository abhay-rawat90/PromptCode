"""Password hashing, cookie sessions, CSRF tokens and the current-user dependencies."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

SESSION_COOKIE = "pc_session"
CSRF_COOKIE = "pc_csrf"
CSRF_HEADER = "X-CSRF-Token"

_hasher = PasswordHasher()  # Argon2id with library-recommended parameters
# Verified against when a user doesn't exist, so login timing doesn't reveal valid usernames.
_DUMMY_HASH = _hasher.hash("timing-equalizer")


# ---------- passwords ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify_legacy_pbkdf2(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Unknown user or Google-only account: burn the same time as a real check.
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except VerificationError:
            pass
        return False
    if stored.startswith("pbkdf2_sha256$"):
        return _verify_legacy_pbkdf2(password, stored)
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return stored.startswith("pbkdf2_sha256$") or _hasher.check_needs_rehash(stored)


# ---------- sessions ----------

def _cookie_kwargs(http_only: bool) -> dict:
    return {
        "httponly": http_only,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_hours * 3600,
    }


def start_session(response: Response, user: User) -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "iat": now,
            "exp": now + timedelta(hours=settings.session_hours),
            "jti": secrets.token_urlsafe(8),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    # The session cookie is invisible to JavaScript (XSS can't steal it); the CSRF cookie is
    # readable so the SPA can echo it back in a header (double-submit pattern).
    response.set_cookie(SESSION_COOKIE, token, **_cookie_kwargs(http_only=True))
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), **_cookie_kwargs(http_only=False))


def end_session(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.cookie_secure, samesite="lax")


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user = db.get(User, int(payload["sub"]))
    if user is None or payload.get("ver") != user.token_version:
        return None  # account deleted or session revoked
    return user


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in to continue.")
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


# ---------- request helpers ----------

def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        # The left-most entries are whatever the client claimed; only the entries appended by
        # our own proxies can be trusted, so count from the right.
        forwarded = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if forwarded:
            return forwarded[-min(settings.trusted_proxy_hops, len(forwarded))]
    return request.client.host if request.client else "unknown"


def csrf_ok(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    return bool(cookie and header and hmac.compare_digest(cookie, header))
