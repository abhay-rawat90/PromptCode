import os
import sys
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database before anything imports it.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
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

    def factory(username: str | None = None, password: str = STRONG_PASSWORD) -> Client:
        c = Client(app)
        if username:
            r = c.post(
                "/api/auth/register",
                json={"username": username, "email": f"{username}@example.com", "password": password},
            )
            assert r.status_code == 201, r.text
        return c

    return factory


@pytest.fixture
def client(make_client):
    return make_client()
