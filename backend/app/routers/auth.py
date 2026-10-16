import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ratelimit
from ..config import settings
from ..db import get_db
from ..models import User, utcnow
from ..schemas import (
    AuthConfig,
    GoogleIn,
    LoginIn,
    MeOut,
    PasswordChangeIn,
    RegisterIn,
    UsernameAvailability,
    UsernameChangeIn,
)
from ..security import (
    client_ip,
    end_session,
    get_current_user,
    hash_password,
    needs_rehash,
    start_session,
    verify_password,
)
from ..validation import password_error_message, password_problems, username_problems

router = APIRouter(prefix="/api/auth", tags=["auth"])

log = logging.getLogger("promptcode.auth")
_google_request = google_requests.Request()
GOOGLE_CLOCK_SKEW_SECONDS = 30


def _find_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.username) == username.lower()))


def _username_taken(db: Session, username: str, exclude_id: int | None = None) -> bool:
    user = _find_by_username(db, username)
    return user is not None and user.id != exclude_id


def _login(response: Response, db: Session, user: User, notice: str | None = None) -> MeOut:
    user.last_login_at = utcnow()
    db.commit()
    start_session(response, user)
    return MeOut.of(user, notice)


@router.get("/config", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    return AuthConfig(
        google_client_id=settings.google_client_id or None,
        daily_test_budget=settings.daily_test_budget,
    )


@router.get("/username-available", response_model=UsernameAvailability)
def username_available(
    request: Request,
    username: str = Query(max_length=64),
    db: Session = Depends(get_db),
) -> UsernameAvailability:
    ratelimit.enforce("username_check_ip", client_ip(request))
    username = username.strip()
    if problems := username_problems(username):
        return UsernameAvailability(available=False, reason="Username " + "; ".join(problems) + ".")
    if _username_taken(db, username):
        return UsernameAvailability(available=False, reason="That username is already taken.")
    return UsernameAvailability(available=True)


@router.post("/register", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    ratelimit.enforce("register_ip", client_ip(request))
    if _username_taken(db, body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken.")
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account with this email already exists. Log in instead (or use Google sign-in).",
        )
    user = User(username=body.username, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # lost a race with a concurrent registration
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That username or email was just taken.")
    return _login(response, db, user)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    identifier = body.login.strip().lower()
    ratelimit.enforce("login_ip", client_ip(request))
    ratelimit.enforce(
        "login_account",
        identifier,
        message="Too many failed attempts for this account. Wait a few minutes and try again.",
    )

    user = db.scalar(select(User).where(User.email == identifier)) if "@" in identifier else None
    user = user or _find_by_username(db, identifier)
    if not verify_password(body.password, user.password_hash if user else None):
        if user and user.password_hash is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "This account uses Google sign-in. Continue with Google, or set a password in Settings.",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username/email or password.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    return _login(response, db, user)


def _username_from_google(db: Session, info: dict) -> str:
    base = info.get("given_name") or info.get("email", "").split("@")[0]
    base = re.sub(r"[^A-Za-z0-9_]", "", base.replace(".", "_").replace("-", "_"))
    base = re.sub(r"_+", "_", base).strip("_")[:15]
    if not base or not base[0].isalpha():
        base = ("user" + base)[:15]
    candidate = base if len(base) >= 3 else base + "user"
    while username_problems(candidate) or _username_taken(db, candidate):
        candidate = f"{base[:15]}{secrets.randbelow(9000) + 1000}"
    return candidate


@router.post("/google", response_model=MeOut)
def google_login(body: GoogleIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    ratelimit.enforce("google_ip", client_ip(request))
    if not settings.google_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google sign-in is not enabled on this server.")
    try:
        info = id_token.verify_oauth2_token(
            body.credential,
            _google_request,
            settings.google_client_id,
            # Google's clock and ours are never perfectly in sync; without slack a token
            # issued a second "in the future" is rejected as "Token used too early".
            clock_skew_in_seconds=GOOGLE_CLOCK_SKEW_SECONDS,
        )
    except google_exceptions.TransportError:
        log.warning("Google sign-in: could not fetch Google's signing certificates", exc_info=True)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't reach Google to verify sign-in. Please try again.")
    except ValueError as exc:
        # Wrong audience (client ID mismatch), expired, bad signature, clock skew…
        log.warning("Google sign-in rejected: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Please try again.")
    if not info.get("email_verified"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your Google email address is not verified.")

    email = info["email"].lower()
    notice = None
    user = db.scalar(select(User).where(User.google_sub == info["sub"]))
    if user is None:
        # Link to an existing account with the same (Google-verified) email, or create one.
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(username=_username_from_google(db, info), email=email)
            db.add(user)
        elif not user.email_verified:
            # Pre-hijacking defence: anyone could have registered this email with a password,
            # since signup doesn't verify emails. Google just proved who owns it, so drop the
            # unverified password and sign out every other session.
            if user.password_hash is not None:
                notice = (
                    "We linked Google to your existing account. For your security the old password "
                    "was removed - you can set a new one in Settings."
                )
            user.password_hash = None
            user.token_version += 1
        user.google_sub = info["sub"]
    user.email_verified = True
    user.avatar_url = info.get("picture") or user.avatar_url
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Sign-in conflict, please try again.")
    return _login(response, db, user, notice)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    end_session(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    user.token_version += 1
    db.commit()
    end_session(response)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)) -> MeOut:
    return MeOut.of(user)


@router.patch("/me/username", response_model=MeOut)
def change_username(
    body: UsernameChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MeOut:
    ratelimit.enforce("account_change", str(user.id))
    if _username_taken(db, body.username, exclude_id=user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken.")
    user.username = body.username
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That username was just taken.")
    return MeOut.of(user)


@router.post("/me/password", response_model=MeOut)
def change_password(
    body: PasswordChangeIn,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MeOut:
    ratelimit.enforce("account_change", str(user.id))
    if user.password_hash is not None and not verify_password(body.current_password or "", user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    if problems := password_problems(body.new_password, user.username, user.email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, password_error_message(problems))
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # sign out every other device
    db.commit()
    start_session(response, user)  # keep this device signed in
    return MeOut.of(user)
