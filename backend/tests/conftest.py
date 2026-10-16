import os
import sys
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database before anything imports it. CI also runs the suite
# against Postgres by setting TEST_DATABASE_URL (it must point at an empty database).
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
# Never call the real Gemini API from tests (the developer's .env may hold a real key).
os.environ["GEMINI_API_KEY"] = ""
os.environ["DAILY_SCHEDULER"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.main import app  # noqa: E402
from app.security import CSRF_COOKIE  # noqa: E402

STRONG_PASSWORD = "Gl4cier-Vortex!"


def email_token(to: str, kind: str = "verify-email") -> str:
    """The token from the latest email of this kind sent to `to` (console backend outbox)."""
    import re

    from app import emails

    for message in reversed(emails.outbox):
        if message.to == to and f"/{kind}?token=" in message.text:
            return re.search(rf"/{kind}\?token=([\w-]+)", message.text).group(1)
    raise AssertionError(f"no {kind} email sent to {to}")


class Client(TestClient):
    """TestClient that echoes the CSRF cookie like the real frontend does."""

    def request(self, method, url, **kwargs):
        token = self.cookies.get(CSRF_COOKIE)
        if token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            kwargs["headers"] = {"X-CSRF-Token": token, **(kwargs.get("headers") or {})}
        return super().request(method, url, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _app_started():
    with TestClient(app):  # runs startup: migrations + seed
        yield


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    ratelimit.reset()


@pytest.fixture
def make_client():
    """Each client has its own cookie jar, i.e. its own browser."""

    def factory(username: str | None = None, password: str = STRONG_PASSWORD, verified: bool = True) -> Client:
        c = Client(app)
        if username:
            email = f"{username}@example.com".lower()
            r = c.post("/api/auth/register", json={"username": username, "email": email, "password": password})
            assert r.status_code == 201, r.text
            if verified:  # click the link from the verification email, like a real user
                assert c.post("/api/auth/verify-email", json={"token": email_token(email)}).status_code == 200
        return c

    return factory


@pytest.fixture
def client(make_client):
    return make_client()
