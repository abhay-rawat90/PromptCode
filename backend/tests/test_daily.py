import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app import daily, llm
from app.config import settings
from app.db import SessionLocal
from app.models import DailyChallenge, DailyQueueItem, Problem, User

DAY1 = date(2031, 3, 3)


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    """Controllable 'now' for the daily module."""
    current = {"t": at(DAY1)}
    monkeypatch.setattr(daily, "now", lambda: current["t"])

    def set_day(day: date, hour: int = 12):
        current["t"] = at(day, hour)

    return set_day


@pytest.fixture(autouse=True)
def clean_daily_tables():
    daily._retry_not_before.clear()
    with SessionLocal() as db:
        ids = db.scalars(select(DailyChallenge.problem_id)).all()
        db.execute(delete(DailyChallenge))
        db.execute(delete(Problem).where(Problem.id.in_(ids)))
        db.execute(delete(DailyQueueItem))
        db.commit()
    object.__setattr__(settings, "gemini_api_key", "fake-key")
    yield
    object.__setattr__(settings, "gemini_api_key", "")


@pytest.fixture
def fake_model(monkeypatch):
    """A 'model' that uppercases the input only when the prompt is the reference solution."""

    async def run_prompt(prompt, test_input):
        text = test_input.upper() if "SHOUT" in prompt else test_input
        return llm.LLMResult(text=text, input_tokens=5, output_tokens=5)

    monkeypatch.setattr(llm, "run_prompt", run_prompt)


def generated(title="Shouting Echo", expected_transform=str.upper, reference="SHOUT: {{input}}", difficulty="easy"):
    words = ["alpha", "bravo", "charlie", "delta", "echo"]
    return llm.GeneratedProblem(
        title=title,
        description="Repeat the input in upper case, nothing else.",
        difficulty=difficulty,
        tags=["format"],
        starter_prompt="",
        reference_prompt=reference,
        test_cases=[
            llm.GeneratedTest(input=w, check_type="exact", expected=expected_transform(w), case_sensitive=True, is_sample=i < 2)
            for i, w in enumerate(words)
        ],
    )


def queue_generations(monkeypatch, *problems):
    briefs = []
    pending = list(problems)

    async def generate_problem(brief):
        briefs.append(brief)
        return (pending.pop(0) if pending else None), None, llm.LLMResult()

    monkeypatch.setattr(llm, "generate_problem", generate_problem)
    return briefs


def make_admin(username: str):
    with SessionLocal() as db:
        db.scalar(select(User).where(func.lower(User.username) == username.lower())).is_admin = True
        db.commit()


QUEUED = {
    "title": "Curated Daily",
    "description": "Hand written.",
    "difficulty": "medium",
    "tags": ["curated"],
    "test_cases": [{"input": "x", "check_type": "exact", "expected": "y", "is_sample": True}],
}


# ---------- publishing ----------

def test_queue_is_used_before_ai(make_client, clock, monkeypatch):
    briefs = queue_generations(monkeypatch, generated())
    admin = make_client("queue_admin")
    make_admin("queue_admin")
    assert make_client("not_admin").post("/api/admin/daily/queue", json=QUEUED).status_code == 403
    assert admin.post("/api/admin/daily/queue", json=QUEUED).status_code == 201

    challenge = asyncio.run(daily.ensure_daily(DAY1))
    assert challenge.source == "queue" and briefs == []  # AI not called

    today = admin.get("/api/daily/today").json()
    assert today["status"] == "ready" and today["problem"]["title"] == "Curated Daily"
    assert "daily" in today["problem"]["tags"]
    assert admin.get("/api/admin/daily/queue").json()[0]["used_on"] == DAY1.isoformat()


def test_ai_problem_is_verified_and_published(client, clock, fake_model, monkeypatch):
    queue_generations(monkeypatch, generated())
    challenge = asyncio.run(daily.ensure_daily(DAY1))
    assert challenge.source == "ai"
    detail = client.get(f"/api/problems/{challenge.problem.slug}").json()
    assert detail["daily_day"] == DAY1.isoformat()
    assert len(detail["sample_tests"]) == 2 and detail["hidden_count"] == 3
    # Idempotent: a second call returns the same challenge without generating again.
    assert asyncio.run(daily.ensure_daily(DAY1)).id == challenge.id


def test_failed_verification_is_fed_back_and_retried(clock, fake_model, monkeypatch):
    broken = generated(title="Broken Expectations", expected_transform=lambda w: w + "!")
    briefs = queue_generations(monkeypatch, broken, generated(title="Fixed Version"))
    challenge = asyncio.run(daily.ensure_daily(DAY1))
    assert challenge.problem.title == "Fixed Version"
    assert len(briefs) == 2 and "reference prompt failed" in briefs[1]


def test_trivially_passable_problem_is_rejected(clock, fake_model, monkeypatch):
    # Expected output == input, so a prompt that just repeats the input passes everything.
    trivial = generated(title="Too Easy", expected_transform=lambda w: w, reference="{{input}}")
    briefs = queue_generations(monkeypatch, trivial, trivial, trivial)
    assert asyncio.run(daily.ensure_daily(DAY1)) is None
    assert len(briefs) == settings.daily_generation_attempts
    assert "too trivial" in briefs[-1]


def test_tomorrows_problem_stays_hidden_until_its_day(client, clock, fake_model, monkeypatch):
    queue_generations(monkeypatch, generated(title="Today One"), generated(title="Tomorrow One"))
    asyncio.run(daily.ensure_upcoming())
    titles = {p["title"] for p in client.get("/api/problems").json()}
    assert "Today One" in titles and "Tomorrow One" not in titles
    with SessionLocal() as db:
        tomorrow_slug = db.scalar(select(Problem.slug).where(Problem.title == "Tomorrow One"))
    assert client.get(f"/api/problems/{tomorrow_slug}").status_code == 404
    assert all(item["problem"]["title"] != "Tomorrow One" for item in client.get("/api/daily/archive").json())

    clock(DAY1 + timedelta(days=1), hour=0)
    assert client.get(f"/api/problems/{tomorrow_slug}").status_code == 200
    assert client.get("/api/daily/today").json()["problem"]["title"] == "Tomorrow One"


def test_today_reports_preparing_when_missing(client, clock, monkeypatch):
    queue_generations(monkeypatch)  # generator returns nothing
    body = client.get("/api/daily/today").json()
    assert body["status"] == "preparing" and body["problem"] is None
    assert body["next_reset"].startswith((DAY1 + timedelta(days=1)).isoformat())


# ---------- streaks ----------

def _solve_today(c):
    slug = c.get("/api/daily/today").json()["problem"]["slug"]
    return c.post(f"/api/problems/{slug}/submit", json={"prompt": "SHOUT: {{input}}"}).json()


def test_streaks_grow_break_and_remember_best(make_client, clock, fake_model, monkeypatch):
    days = [DAY1 + timedelta(days=i) for i in range(5)]
    queue_generations(monkeypatch, *[generated(title=f"Streak Day {i}") for i in range(5)])
    for d in days:
        asyncio.run(daily.ensure_daily(d))
    user = make_client("streaker")

    clock(days[0])
    first = _solve_today(user)
    assert first["status"] == "accepted" and first["streak_extended"] and first["current_streak"] == 1
    assert _solve_today(user)["streak_extended"] is False  # solving again the same day doesn't count twice

    clock(days[1])
    assert _solve_today(user)["current_streak"] == 2

    clock(days[3])  # skipped days[2]
    assert user.get("/api/daily/today").json()["current_streak"] == 0
    assert _solve_today(user)["current_streak"] == 1

    me = user.get("/api/auth/me").json()
    assert me["current_streak"] == 1 and me["best_streak"] == 2
    profile = user.get("/api/users/streaker").json()
    assert profile["dailies_solved"] == 3 and profile["best_streak"] == 2

    archive = user.get("/api/daily/archive").json()
    by_day = {a["day"]: a for a in archive}
    assert [a["day"] for a in archive] == [d.isoformat() for d in reversed(days[:4])]  # nothing from the future
    assert by_day[days[0].isoformat()]["solved_on_day"] and not by_day[days[2].isoformat()]["solved"]

    # Solving an old daily later counts as solved, but not toward the streak.
    old_slug = by_day[days[2].isoformat()]["problem"]["slug"]
    late = user.post(f"/api/problems/{old_slug}/submit", json={"prompt": "SHOUT: {{input}}"}).json()
    assert late["status"] == "accepted" and not late["streak_extended"]
    by_day = {a["day"]: a for a in user.get("/api/daily/archive").json()}
    assert by_day[days[2].isoformat()]["solved"] and not by_day[days[2].isoformat()]["solved_on_day"]


def test_quota_exhaustion_stops_generation_and_backs_off(client, clock, monkeypatch):
    calls = []

    async def generate_problem(brief):
        calls.append(brief)
        return None, "The AI model's daily quota is used up", llm.LLMResult(quota_exhausted=True)

    monkeypatch.setattr(llm, "generate_problem", generate_problem)
    assert asyncio.run(daily.ensure_daily(DAY1)) is None
    assert len(calls) == 1  # stopped at once instead of burning every attempt
    assert client.get("/api/daily/today").json()["status"] == "unavailable"

    asyncio.run(daily.ensure_daily(DAY1))
    assert len(calls) == 1  # still backing off: no new request to the API

    clock(DAY1, hour=15)  # past the back-off window
    asyncio.run(daily.ensure_daily(DAY1))
    assert len(calls) == 2


def test_verification_infra_error_aborts_without_retrying(clock, monkeypatch):
    briefs = queue_generations(monkeypatch, generated(), generated(title="Second"))

    async def run_prompt(prompt, test_input):
        return llm.LLMResult(error="The AI model's daily quota is used up", quota_exhausted=True)

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    assert asyncio.run(daily.ensure_daily(DAY1)) is None
    assert len(briefs) == 1
