import pytest

from app.security import CSRF_COOKIE, SESSION_COOKIE, hash_password, verify_password
from app.validation import password_problems, username_problems
from conftest import STRONG_PASSWORD


# ---------- rules ----------

@pytest.mark.parametrize("name", ["ab", "1abc", "has space", "bad-dash", "double__under", "trailing_", "admin", "x" * 21])
def test_bad_usernames(name):
    assert username_problems(name)


@pytest.mark.parametrize("name", ["abc", "Alice_99", "prompt_wizard"])
def test_good_usernames(name):
    assert not username_problems(name)


@pytest.mark.parametrize(
    "password",
    ["short1!A", "alllowercase1!", "ALLUPPERCASE1!", "NoNumbers!!", "NoSymbols123", "Password1!", " Lead1ng!sp"],
)
def test_weak_passwords(password):
    problems = password_problems(password)
    if password == "short1!A":  # exactly 8 chars and otherwise fine
        assert not problems
    else:
        assert problems


def test_password_cannot_contain_username():
    assert "to not contain your username or email" in password_problems("Alice#2026x", username="alice")


def test_argon2_and_legacy_hashes():
    assert verify_password("S3cret!pw", hash_password("S3cret!pw"))
    assert not verify_password("wrong", hash_password("S3cret!pw"))
    legacy = "pbkdf2_sha256$1000$salt$" + __import__("hashlib").pbkdf2_hmac("sha256", b"pw", b"salt", 1000).hex()
    assert verify_password("pw", legacy)
    assert not verify_password("pw", None)


# ---------- register / login ----------

def test_register_sets_httponly_session_and_csrf_cookie(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "cookie_user", "email": "Cookie@Example.com", "password": STRONG_PASSWORD},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "cookie@example.com"
    assert "access_token" not in r.json()  # token never exposed to JavaScript
    set_cookie = r.headers.get_list("set-cookie")
    session = next(c for c in set_cookie if c.startswith(SESSION_COOKIE))
    csrf = next(c for c in set_cookie if c.startswith(CSRF_COOKIE))
    assert "HttpOnly" in session and "SameSite=lax" in session
    assert "HttpOnly" not in csrf
    assert client.get("/api/auth/me").json()["username"] == "cookie_user"


def test_register_field_errors(client):
    r = client.post("/api/auth/register", json={"username": "ok_name", "email": "nope", "password": "weak"})
    assert r.status_code == 422
    fields = r.json()["fields"]
    assert "email" in fields and fields["password"].startswith("Password needs")


def test_usernames_are_case_insensitive_unique(make_client, client):
    make_client("CaseUser")
    r = client.post(
        "/api/auth/register",
        json={"username": "caseuser", "email": "other@example.com", "password": STRONG_PASSWORD},
    )
    assert r.status_code == 409
    assert client.get("/api/auth/username-available", params={"username": "CASEUSER"}).json()["available"] is False
    assert client.get("/api/auth/username-available", params={"username": "fresh_name"}).json()["available"] is True


def test_login_with_username_or_email(make_client, client):
    make_client("loginuser")
    assert client.post("/api/auth/login", json={"login": "LoginUser", "password": STRONG_PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"login": "loginuser@example.com", "password": STRONG_PASSWORD}).status_code == 200
    r = client.post("/api/auth/login", json={"login": "loginuser", "password": "wrong"})
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect username/email or password."
    # Unknown users get the identical message (no account enumeration).
    r = client.post("/api/auth/login", json={"login": "ghost", "password": "wrong"})
    assert r.json()["detail"] == "Incorrect username/email or password."


def test_login_brute_force_is_throttled(make_client, client):
    make_client("target")
    codes = [client.post("/api/auth/login", json={"login": "target", "password": f"guess{i}"}).status_code for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[-1] == 429


# ---------- CSRF / origin ----------

def test_mutations_without_csrf_header_are_rejected(make_client):
    c = make_client("csrf_user")
    r = c.post("/api/auth/logout-all", headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    # Bypass the helper that adds the header automatically.
    from fastapi.testclient import TestClient

    r = TestClient.request(c, "POST", "/api/auth/logout-all")
    assert r.status_code == 403


def test_cross_origin_post_blocked(client):
    r = client.post("/api/auth/login", json={"login": "a", "password": "b"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"


# ---------- account management ----------

def test_change_password_revokes_other_sessions(make_client):
    laptop = make_client("multi_device")
    phone = make_client()
    assert phone.post("/api/auth/login", json={"login": "multi_device", "password": STRONG_PASSWORD}).status_code == 200

    assert laptop.post("/api/auth/me/password", json={"current_password": "wrong", "new_password": "N3w-Passphrase!"}).status_code == 400
    r = laptop.post("/api/auth/me/password", json={"current_password": STRONG_PASSWORD, "new_password": "N3w-Passphrase!"})
    assert r.status_code == 200
    assert laptop.get("/api/auth/me").status_code == 200  # this device stays signed in
    assert phone.get("/api/auth/me").status_code == 401  # the other one is signed out


def test_logout_all(make_client):
    c = make_client("logout_all_user")
    assert c.post("/api/auth/logout-all").status_code == 204
    assert c.get("/api/auth/me").status_code == 401


def test_change_username(make_client):
    make_client("taken_name")
    c = make_client("renamer")
    assert c.patch("/api/auth/me/username", json={"username": "Taken_Name"}).status_code == 409
    assert c.patch("/api/auth/me/username", json={"username": "root"}).status_code == 422
    assert c.patch("/api/auth/me/username", json={"username": "renamed_ok"}).json()["username"] == "renamed_ok"


# ---------- Google ----------

def _fake_google(monkeypatch, **claims):
    from app.routers import auth as auth_router

    info = {"sub": "google-123", "email": "gina.g@gmail.com", "email_verified": True, "given_name": "Gina", **claims}

    def verify(token, request, audience, clock_skew_in_seconds=0):
        if token != "valid-google-token":
            raise ValueError("bad token")
        assert audience == "test-client-id.apps.googleusercontent.com"
        assert clock_skew_in_seconds > 0
        return info

    monkeypatch.setattr(auth_router.id_token, "verify_oauth2_token", verify)


def test_google_sign_in_creates_then_reuses_account(client, make_client, monkeypatch):
    _fake_google(monkeypatch)
    assert client.post("/api/auth/google", json={"credential": "forged-google-token"}).status_code == 401

    r = client.post("/api/auth/google", json={"credential": "valid-google-token"})
    assert r.status_code == 200
    me = r.json()
    assert me["has_google"] and not me["has_password"] and me["username"] == "Gina"

    again = make_client().post("/api/auth/google", json={"credential": "valid-google-token"}).json()
    assert again["id"] == me["id"]

    # Password login explains the account uses Google.
    r = make_client().post("/api/auth/login", json={"login": "gina.g@gmail.com", "password": "Whatever1!"})
    assert "Google" in r.json()["detail"]

    # A Google-only user can set a password without a current one.
    assert client.post("/api/auth/me/password", json={"new_password": "Fresh-Start-99"}).json()["has_password"]


def test_google_links_existing_email_account_and_blocks_pre_hijacking(make_client, monkeypatch):
    # An "attacker" registers the victim's email with a password they know (but can't verify it)...
    attacker = make_client("linkme", verified=False)
    # ...then the real owner signs in with Google, proving they own the email.
    _fake_google(monkeypatch, sub="google-link", email="linkme@example.com")
    owner = make_client()
    me = owner.post("/api/auth/google", json={"credential": "valid-google-token"}).json()
    assert me["username"] == "linkme" and me["has_google"]
    assert not me["has_password"] and "password was removed" in me["notice"]
    # The attacker's session and password no longer work; the owner stays signed in.
    assert attacker.get("/api/auth/me").status_code == 401
    r = make_client().post("/api/auth/login", json={"login": "linkme", "password": "Gl4cier-Vortex!"})
    assert r.status_code == 401
    assert owner.get("/api/auth/me").status_code == 200

    # Once verified, signing in with Google again changes nothing and shows no notice.
    owner.post("/api/auth/me/password", json={"new_password": "Owner-Chosen-77"})
    again = make_client().post("/api/auth/google", json={"credential": "valid-google-token"}).json()
    assert again["has_password"] and again["notice"] is None


def test_google_unverified_email_rejected(client, monkeypatch):
    _fake_google(monkeypatch, sub="google-unverified", email_verified=False)
    assert client.post("/api/auth/google", json={"credential": "valid-google-token"}).status_code == 401


def test_migrations_keep_case_insensitive_username_index():
    # Regression: an Alembic batch rebuild on SQLite once dropped this expression index.
    from sqlalchemy import inspect, text

    from app.db import engine

    query = (
        "select name from sqlite_master where type='index'"
        if engine.dialect.name == "sqlite"
        else "select indexname from pg_indexes where tablename = 'users'"
    )
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(text(query))}
    assert "ix_users_username_lower" in names
    assert "email_verified" in {c["name"] for c in inspect(engine).get_columns("users")}


def _signed_google_token(iat_offset: int, audience="test-client-id.apps.googleusercontent.com"):
    """A real RS256 ID token plus the matching certificate, as Google would serve them."""
    import datetime as dt
    import time

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth import crypt, jwt as google_jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = crypt.RSASigner.from_string(pem_key, key_id="kid1")
    t = int(time.time()) + iat_offset
    token = google_jwt.encode(signer, {
        "iss": "https://accounts.google.com", "aud": audience, "sub": "real-sig-user",
        "email": "realsig@gmail.com", "email_verified": True, "iat": t, "exp": t + 3600,
    })
    return token.decode(), {"kid1": cert.public_bytes(serialization.Encoding.PEM).decode()}


def test_google_token_from_slightly_fast_clock_is_accepted(client, monkeypatch):
    """Regression: Google's clock 2s ahead of ours used to fail with 'Token used too early'."""
    from google.oauth2 import id_token as google_id_token

    token, certs = _signed_google_token(iat_offset=2)
    monkeypatch.setattr(google_id_token, "_fetch_certs", lambda request, url: certs)
    r = client.post("/api/auth/google", json={"credential": token})
    assert r.status_code == 200, r.text
    assert r.json()["has_google"]


def test_google_token_with_wrong_audience_is_rejected(client, monkeypatch):
    from google.oauth2 import id_token as google_id_token

    token, certs = _signed_google_token(iat_offset=0, audience="someone-elses-app.apps.googleusercontent.com")
    monkeypatch.setattr(google_id_token, "_fetch_certs", lambda request, url: certs)
    assert client.post("/api/auth/google", json={"credential": token}).status_code == 401



def test_google_link_keeps_password_of_verified_account(make_client, monkeypatch):
    """A user who verified their email owns it, so linking Google must not touch their password."""
    make_client("verified_owner")  # verified via the email link
    _fake_google(monkeypatch, sub="google-verified-owner", email="verified_owner@example.com")
    me = make_client().post("/api/auth/google", json={"credential": "valid-google-token"}).json()
    assert me["username"] == "verified_owner" and me["has_password"] and me["notice"] is None
    assert make_client().post("/api/auth/login", json={"login": "verified_owner", "password": "Gl4cier-Vortex!"}).status_code == 200
