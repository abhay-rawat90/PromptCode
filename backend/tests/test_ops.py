"""Operations: the uptime endpoint, copying the database, and a scheduler that lets the database sleep."""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select

from app import daily, dbcopy, main, reminders
from app.config import settings
from app.models import Problem, User


# ---------- uptime ----------

def test_uptime_works_with_get_and_head_without_touching_the_database(client, monkeypatch):
    def no_database():
        raise AssertionError("the plain uptime check must not use the database")

    monkeypatch.setattr(main.engine, "connect", no_database)
    r = client.get("/api/uptime")
    assert r.status_code == 200 and r.json()["status"] == "ok" and "database" not in r.json()
    assert client.head("/api/uptime").status_code == 200
    assert client.head("/api/health").status_code == 200


def test_uptime_database_check(client):
    r = client.get("/api/uptime", params={"db": 1})
    assert r.status_code == 200 and r.json()["database"] == "ok"


def test_uptime_reports_database_down(client, monkeypatch):
    def broken():
        raise OSError("connection refused")

    monkeypatch.setattr(main.engine, "connect", broken)
    r = client.get("/api/uptime", params={"db": 1})
    assert r.status_code == 503 and r.json() == {"status": "error", "database": "unreachable"}


def test_uptime_database_checks_are_rate_limited(client):
    codes = [client.get("/api/uptime", params={"db": 1}).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert client.get("/api/uptime").status_code == 200  # the plain check isn't limited


# ---------- copying the database ----------

def test_copy_database_to_a_backup_file(make_client, tmp_path):
    make_client("copied_user")
    target = f"sqlite:///{(tmp_path / 'backup.db').as_posix()}"
    copied = dbcopy.copy_database(settings.database_url, target, log=lambda *_: None)

    backup = create_engine(target)
    with backup.connect() as conn:
        assert conn.execute(select(func.count()).select_from(User)).scalar() == copied["users"] >= 1
        assert conn.execute(select(func.count()).select_from(Problem)).scalar() == copied["problems"] >= 5
        assert conn.execute(select(User.id).where(User.username == "copied_user")).scalar()
    assert "llm_cache" not in copied  # skipped unless asked for

    with pytest.raises(ValueError, match="already has data"):
        dbcopy.copy_database(settings.database_url, target, log=lambda *_: None)
    again = dbcopy.copy_database(settings.database_url, target, replace=True, include_cache=True, log=lambda *_: None)
    assert again["users"] == copied["users"] and "llm_cache" in again
    backup.dispose()


@pytest.mark.skipif(not settings.database_url.startswith("postgresql"), reason="needs Postgres (CI)")
def test_copy_between_postgres_databases_moves_id_sequences(make_client):
    """Postgres to Postgres, like moving from Render to Neon: after copying rows with their ids,
    new rows must get fresh ids instead of colliding with copied ones."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    make_client("pg_copied_user")
    admin_url = make_url(settings.database_url)
    target_url = admin_url.set(database="promptcode_copy_test")
    server = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS promptcode_copy_test"))
        conn.execute(text("CREATE DATABASE promptcode_copy_test"))
    try:
        target = target_url.render_as_string(hide_password=False)
        copied = dbcopy.copy_database(settings.database_url, target, log=lambda *_: None)
        assert copied["users"] >= 1
        copy_engine = create_engine(target)
        with copy_engine.begin() as conn:
            highest = conn.execute(select(func.max(User.id))).scalar()
            new_id = conn.execute(text(
                "INSERT INTO users (username, email, token_version, created_at, current_streak, best_streak) "
                "VALUES ('after_copy', 'after_copy@example.com', 0, now(), 0, 0) RETURNING id"
            )).scalar()
        assert new_id > highest
        copy_engine.dispose()
    finally:
        with server.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS promptcode_copy_test WITH (FORCE)"))
        server.dispose()


def test_copy_database_refuses_the_same_database():
    with pytest.raises(ValueError, match="same database"):
        dbcopy.copy_database(settings.database_url, settings.database_url)


def test_copy_command_line_reports_errors(tmp_path, capsys):
    empty = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    assert dbcopy.main(["--from", empty, "--to", f"sqlite:///{(tmp_path / 'out.db').as_posix()}"]) == 1
    assert "doesn't look like a PromptCode database" in capsys.readouterr().err


# ---------- letting the database sleep ----------

def test_scheduler_stops_querying_once_the_days_are_ready(monkeypatch):
    calls = []

    async def ensure_daily(day):
        calls.append(day)
        return object()  # pretend the problem exists

    monkeypatch.setattr(daily, "ensure_daily", ensure_daily)
    asyncio.run(daily.ensure_upcoming())
    assert len(calls) == 2  # today and tomorrow
    asyncio.run(daily.ensure_upcoming())
    assert len(calls) == 2  # nothing left to check: no database queries


def test_reminders_stop_querying_once_done_for_the_day(monkeypatch):
    clock = datetime(2033, 1, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(daily, "now", lambda: clock)
    reminders.send_due()  # no daily problem that day: nothing to send, but the day isn't done

    monkeypatch.setattr(reminders, "_done_day", clock.date())

    def no_database():
        raise AssertionError("must not query the database after the day's reminders are done")

    monkeypatch.setattr(reminders, "SessionLocal", no_database)
    assert reminders.send_due() == 0
