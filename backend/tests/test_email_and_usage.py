from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app import emails, llm, usage
from app.config import DEV_JWT_SECRET, Settings, _database_url, settings
from app.db import SessionLocal
from app.models import AuthToken, User, utcnow
from conftest import STRONG_PASSWORD, email_token

PROBLEM = {
    "title": "Gate Check",
    "description": "x",
    "difficulty": "easy",
    "test_cases": [{"input": "a", "check_type": "exact", "expected": "b", "is_sample": True}],
}


# ---------- verification ----------

def test_signup_sends_verification_and_gates_publishing(make_client):
    c = make_client("unverified_user", verified=False)
    me = c.get("/api/auth/me").json()
    assert me["email_verified"] is False
    message = emails.outbox[-1]
    assert message.to == "unverified_user@example.com" and "Confirm" in message.subject
    assert "http://localhost:5173/verify-email?token=" in message.text and "verify-email?token=" in message.html

    r = c.post("/api/problems", json=PROBLEM)
    assert r.status_code == 403 and "verify your email" in r.json()["detail"]

    token = email_token("unverified_user@example.com")
    assert c.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert c.get("/api/auth/me").json()["email_verified"] is True
    assert c.post("/api/problems", json=PROBLEM).status_code == 201
    # Links are single-use.
    assert c.post("/api/auth/verify-email", json={"token": token}).status_code == 400


def test_resend_verification_invalidates_older_links(make_client):
    c = make_client("resender", verified=False)
    first = email_token("resender@example.com")
    assert c.post("/api/auth/resend-verification").status_code == 202
    second = email_token("resender@example.com")
    assert first != second
    assert c.post("/api/auth/verify-email", json={"token": first}).status_code == 400
    assert c.post("/api/auth/verify-email", json={"token": second}).status_code == 200
    assert "already verified" in c.post("/api/auth/resend-verification").json()["detail"]


def test_bogus_verification_token(client):
    assert client.post("/api/auth/verify-email", json={"token": "not-a-real-token-123"}).status_code == 400


# ---------- password reset ----------

def test_password_reset_flow(make_client):
    owner = make_client("forgetful")
    other_device = make_client()
    assert other_device.post("/api/auth/login", json={"login": "forgetful", "password": STRONG_PASSWORD}).status_code == 200

    anon = make_client()
    known = anon.post("/api/auth/forgot-password", json={"email": "Forgetful@Example.com"})
    unknown = anon.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()  # can't be used to discover accounts
    assert not any(m.to == "nobody@example.com" for m in emails.outbox)

    token = email_token("forgetful@example.com", "reset-password")
    weak = anon.post("/api/auth/reset-password", json={"token": token, "new_password": "weak"})
    assert weak.status_code == 422
    r = anon.post("/api/auth/reset-password", json={"token": token, "new_password": "Brand-New-Pass9"})
    assert r.status_code == 200 and r.json()["username"] == "forgetful"  # logged in on this device
    assert anon.get("/api/auth/me").status_code == 200

    # Old password dead, other sessions revoked, token single-use.
    assert make_client().post("/api/auth/login", json={"login": "forgetful", "password": STRONG_PASSWORD}).status_code == 401
    assert owner.get("/api/auth/me").status_code == 401 and other_device.get("/api/auth/me").status_code == 401
    assert anon.post("/api/auth/reset-password", json={"token": token, "new_password": "Another-Pass77"}).status_code == 400


def test_reset_tokens_expire_and_only_latest_works(make_client):
    make_client("expiry_user")
    anon = make_client()
    anon.post("/api/auth/forgot-password", json={"email": "expiry_user@example.com"})
    old = email_token("expiry_user@example.com", "reset-password")
    anon.post("/api/auth/forgot-password", json={"email": "expiry_user@example.com"})
    new = email_token("expiry_user@example.com", "reset-password")
    assert anon.post("/api/auth/reset-password", json={"token": old, "new_password": "Fresh-Pass-123"}).status_code == 400

    with SessionLocal() as db:
        db.execute(update(AuthToken).where(AuthToken.used_at.is_(None)).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert anon.post("/api/auth/reset-password", json={"token": new, "new_password": "Fresh-Pass-123"}).status_code == 400


def test_google_only_account_can_set_password_via_reset(make_client, monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(
        auth_router.id_token, "verify_oauth2_token",
        lambda *a, **k: {"sub": "g-reset", "email": "gonly@gmail.com", "email_verified": True, "given_name": "Gonly"},
    )
    make_client().post("/api/auth/google", json={"credential": "x" * 20})
    anon = make_client()
    anon.post("/api/auth/forgot-password", json={"email": "gonly@gmail.com"})
    token = email_token("gonly@gmail.com", "reset-password")
    me = anon.post("/api/auth/reset-password", json={"token": token, "new_password": "Now-I-Have-1"}).json()
    assert me["has_password"] and me["has_google"]


def test_stored_tokens_are_hashed(make_client):
    make_client("hash_check", verified=False)
    raw = email_token("hash_check@example.com")
    with SessionLocal() as db:
        stored = db.scalars(select(AuthToken.token_hash)).all()
    assert raw not in stored and all(len(h) == 64 for h in stored)


# ---------- usage + cap ----------

@pytest.fixture
def fake_llm_with_tokens(monkeypatch):
    class Meta:
        prompt_token_count = 100
        candidates_token_count = 20
        thoughts_token_count = 5

    class Resp:
        usage_metadata = Meta()
        text = "positive"
        candidates = [type("C", (), {"finish_reason": None})()]
        prompt_feedback = None

    class Models:
        async def generate_content(self, **kwargs):
            return Resp()

    class Client:
        class aio:
            models = Models()

    monkeypatch.setattr(llm, "get_client", lambda: Client())


def test_usage_is_tracked_per_user_and_capped(make_client, fake_llm_with_tokens, monkeypatch):
    user = make_client("spender")
    with SessionLocal() as db:
        db.execute(update(User).where(func.lower(User.username) == "spender").values(is_admin=True))
        db.commit()
    before = usage.tokens_today()
    assert user.post("/api/problems/sentiment-label/run", json={"prompt": "x {{input}}"}).status_code == 200
    assert usage.tokens_today() - before == 2 * 125  # 2 sample tests x (100 in + 25 out)

    report = user.get("/api/admin/usage").json()
    assert report["days"][0]["tokens"]["run"] >= 250
    assert report["top_users_today"][0]["username"] == "spender"

    object.__setattr__(settings, "daily_token_cap", usage.tokens_today())
    try:
        r = user.post("/api/problems/sentiment-label/run", json={"prompt": "x {{input}}"})
        assert r.status_code == 503 and "budget" in r.json()["detail"]
    finally:
        object.__setattr__(settings, "daily_token_cap", 0)


def test_admin_usage_requires_admin(make_client):
    assert make_client("plain_user").get("/api/admin/usage").status_code == 403


# ---------- deployment config ----------

def test_database_url_normalization():
    assert _database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert _database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert _database_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"
    assert _database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_production_refuses_unsafe_settings():
    unsafe = Settings(
        environment="production",
        jwt_secret=DEV_JWT_SECRET,
        cookie_secure=False,
        database_url="sqlite:///x.db",
        app_url="http://localhost:5173",
    )
    with pytest.raises(RuntimeError) as exc:
        unsafe.validate()
    for needle in ("JWT_SECRET", "COOKIE_SECURE", "Postgres", "APP_URL"):
        assert needle in str(exc.value)
    Settings(
        environment="production",
        jwt_secret="x" * 40,
        cookie_secure=True,
        database_url="postgresql+psycopg://u@h/db",
        app_url="https://promptcode.example",
        email_backend="resend",
        resend_api_key="re_123",
    ).validate()


def test_client_ip_ignores_spoofed_forwarded_for(monkeypatch):
    from starlette.requests import Request

    from app.security import client_ip

    object.__setattr__(settings, "trust_proxy", True)
    try:
        req = Request({"type": "http", "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")], "client": ("10.0.0.1", 1)})
        assert client_ip(req) == "203.0.113.9"  # what our proxy saw, not what the client claimed
    finally:
        object.__setattr__(settings, "trust_proxy", False)
