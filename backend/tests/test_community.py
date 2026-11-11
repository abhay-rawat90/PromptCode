"""Phase C: hidden model details, hints, solutions, ratings, badges and streak reminders."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app import badges, daily, emails, llm, ratelimit, reminders
from app.config import settings
from app.db import SessionLocal
from app.models import DailyChallenge, Problem, Submission, User
from app.routers import admin as admin_router

SLUG = "sentiment-label"  # a seeded problem with 3 hints and an official solution


@pytest.fixture
def fake_llm(monkeypatch):
    """Passes every sentiment-label test when the prompt contains SOLVE."""
    answers = {"Absolutely": "positive", "Broke": "negative", "It arrived": "neutral", "Not bad": "positive", "I wanted": "negative",
               "A box": "84", "Sam": "28", "A train": "210", "What is 17": "391", "A shirt": "30"}

    async def run_prompt(prompt, test_input):
        text = next((v for k, v in answers.items() if test_input.startswith(k)), "yes") if "SOLVE" in prompt else "nope"
        tokens = 100 if "LONG" in prompt else 1 if "TINY" in prompt else 10
        return llm.LLMResult(text=text, input_tokens=tokens, output_tokens=2, model="gemini:gemini-3.5-flash-lite",
                             backup=True, cached=True)

    async def judge(rubric, test_input, output):
        return True, "ok", llm.LLMResult()

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    monkeypatch.setattr(llm, "judge", judge)


def solve(client, prompt="SOLVE", slug=SLUG):
    out = client.post(f"/api/problems/{slug}/submit", json={"prompt": prompt}).json()
    assert out["status"] == "accepted", out
    return out


def user_row(username):
    with SessionLocal() as db:
        return db.scalar(select(User).where(func.lower(User.username) == username.lower()))


# ---------- C1: solvers never see which model answered ----------

def test_model_details_are_admin_only(make_client, fake_llm):
    out = solve(make_client("plain_solver"))
    assert out["backup_models"] == [] and out["cached"] == 0
    assert all(r["model"] is None and r["backup"] is False and r["cached"] is False for r in out["results"])

    admin_out = solve(make_client("curious_admin", admin=True))
    assert admin_out["backup_models"] == ["gemini-3.5-flash-lite"] and admin_out["cached"] == 5
    assert admin_out["results"][0]["model"] == "gemini-3.5-flash-lite"


def test_waiting_events_do_not_name_the_model(make_client, monkeypatch):
    async def run_prompt(prompt, test_input):
        llm.emit({"type": "waiting", "model": "secret-model", "seconds": 3, "queue": 1})
        return llm.LLMResult(text="positive")

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    r = make_client("waiter").post(f"/api/problems/{SLUG}/run", json={"prompt": "x"},
                                   headers={"Accept": "application/x-ndjson"})
    assert '"waiting"' in r.text and "secret-model" not in r.text


# ---------- C2: hints ----------

def test_hints_open_one_at_a_time(make_client):
    user = make_client("hint_user")
    detail = user.get(f"/api/problems/{SLUG}").json()
    assert detail["hint_count"] == 3 and detail["hints"] == []
    assert any(p["slug"] == SLUG and p["has_hints"] for p in user.get("/api/problems").json())

    assert make_client().post(f"/api/problems/{SLUG}/hints/1").status_code == 401
    assert user.post(f"/api/problems/{SLUG}/hints/2").status_code == 400  # in order
    first = user.post(f"/api/problems/{SLUG}/hints/1").json()
    assert len(first["hints"]) == 1 and first["hint_count"] == 3
    assert len(user.post(f"/api/problems/{SLUG}/hints/2").json()["hints"]) == 2
    assert len(user.post(f"/api/problems/{SLUG}/hints/1").json()["hints"]) == 2  # re-opening changes nothing
    assert user.post(f"/api/problems/{SLUG}/hints/4").status_code == 404
    assert len(user.get(f"/api/problems/{SLUG}").json()["hints"]) == 2  # remembered


def test_hints_cannot_leak_hidden_tests_or_the_solution(make_client, monkeypatch):
    async def run_prompt(prompt, test_input):
        return llm.LLMResult(text="yes" if "SOLVE" in prompt else "no")

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    admin = make_client("hint_author", admin=True)
    body = {
        "title": "Hinted Problem", "description": "Answer yes.", "difficulty": "medium", "tags": [],
        "reference_prompt": "SOLVE this by always answering yes",
        "test_cases": [
            {"input": "a visible sample input", "check_type": "exact", "expected": "yes", "is_sample": True},
            {"input": "the secret hidden input", "check_type": "exact", "expected": "yes"},
        ],
    }
    leak_test = admin.post("/api/problems", json={**body, "hints": ["Try: the secret hidden input"]})
    assert leak_test.status_code == 422 and "Hint 1 gives away" in leak_test.text
    leak_ref = admin.post("/api/problems", json={**body, "hints": ["ok", "Use: SOLVE this by always answering yes"]})
    assert leak_ref.status_code == 422 and "Hint 2" in leak_ref.text
    assert admin.post("/api/problems", json={**body, "hints": ["a", "b", "c", "d"]}).status_code == 422

    ok = admin.post("/api/problems", json={**body, "hints": ["  Mention a visible sample input.  ", ""]})
    assert ok.status_code == 201 and ok.json()["hint_count"] == 1
    assert admin.get(f"/api/problems/{ok.json()['slug']}/full").json()["hints"] == ["Mention a visible sample input."]


def test_generated_daily_keeps_only_safe_hints(monkeypatch):
    from test_daily import generated

    spec = generated()
    from app.schemas import safe_hints

    hints = safe_hints(spec.hints, spec.description, spec.reference_prompt, spec.test_cases)
    assert hints == ["Say exactly what the output must look like."]  # the one quoting a hidden answer is dropped


def test_hint_suggestions(make_client, monkeypatch):
    async def suggest(problem):
        return ["Look at the format.", "Look at the format.", "Name the labels.", "Third.", "Fourth."], None

    monkeypatch.setattr(llm, "suggest_hints", suggest)
    body = {"title": "Any", "description": "d", "difficulty": "easy", "tags": [], "reference_prompt": "r",
            "test_cases": [{"input": "", "check_type": "exact", "expected": "x", "is_sample": True}]}
    assert make_client("no_suggest").post("/api/problems/suggest-hints", json=body).status_code == 403
    out = make_client("suggester", admin=True).post("/api/problems/suggest-hints", json=body).json()
    assert out["hints"] == ["Look at the format.", "Name the labels.", "Third."]


# ---------- C4: solutions ----------

def test_solutions_unlock_after_solving_and_sharing_is_opt_in(make_client, fake_llm):
    alice, bob, carol = make_client("sol_alice"), make_client("sol_bob"), make_client("sol_carol")
    locked = carol.get(f"/api/problems/{SLUG}/solutions")
    assert locked.status_code == 403 and "unlock" in locked.json()["detail"]

    long_id = solve(alice, "SOLVE LONG")["submission_id"]
    short_id = solve(alice, "SOLVE")["submission_id"]
    bob_id = solve(bob, "SOLVE LONG bob")["submission_id"]

    view = bob.get(f"/api/problems/{SLUG}/solutions").json()
    assert view["official"].startswith("Classify the sentiment") and view["community"] == []  # nothing shared yet
    assert view["my_best_id"] == bob_id and view["my_shared_id"] is None

    # Sharing: own accepted submissions only; one per problem.
    assert bob.post(f"/api/submissions/{long_id}/share", json={"shared": True}).status_code == 404
    failed = carol.post(f"/api/problems/{SLUG}/submit", json={"prompt": "bad"}).json()["submission_id"]
    assert carol.post(f"/api/submissions/{failed}/share", json={"shared": True}).status_code == 400
    assert alice.post(f"/api/submissions/{long_id}/share", json={"shared": True}).json() == {"shared": True}
    assert alice.post(f"/api/submissions/{short_id}/share", json={"shared": True}).status_code == 200
    assert bob.post(f"/api/submissions/{bob_id}/share", json={"shared": True}).status_code == 200

    view = bob.get(f"/api/problems/{SLUG}/solutions").json()
    assert [s["id"] for s in view["community"]] == [short_id, bob_id]  # fewest tokens first; alice's old one replaced
    assert view["community"][0]["username"] == "sol_alice" and view["community"][1]["mine"] is True
    assert view["my_shared_id"] == bob_id

    # Votes: toggle, not your own, solvers only.
    assert bob.post(f"/api/solutions/{bob_id}/vote").status_code == 400
    assert carol.post(f"/api/solutions/{short_id}/vote").status_code == 403
    assert bob.post(f"/api/solutions/{short_id}/vote").json() == {"votes": 1, "voted": True}
    assert bob.get(f"/api/problems/{SLUG}/solutions").json()["community"][0]["voted"] is True
    assert bob.post(f"/api/solutions/{short_id}/vote").json() == {"votes": 0, "voted": False}

    # Stop sharing; admins can remove one.
    assert bob.post(f"/api/submissions/{bob_id}/share", json={"shared": False}).json() == {"shared": False}
    admin = make_client("sol_admin", admin=True)
    assert bob.post(f"/api/admin/solutions/{short_id}/remove").status_code == 403
    assert admin.post(f"/api/admin/solutions/{short_id}/remove").status_code == 204
    assert admin.get(f"/api/problems/{SLUG}/solutions").json()["community"] == []


# ---------- C5: ratings ----------

def test_ratings_and_admin_feedback(make_client, fake_llm, monkeypatch):
    monkeypatch.setattr(admin_router, "MIN_FEEDBACK_VOTES", 2)
    slug = "exact-arithmetic"
    raters = [make_client(f"rater_{i}") for i in range(3)]
    assert raters[0].put(f"/api/problems/{slug}/rating", json={"liked": True}).status_code == 403  # not solved yet
    for r in raters:
        solve(r, slug=slug)

    out = raters[0].put(f"/api/problems/{slug}/rating", json={"liked": True, "difficulty": "right"}).json()
    assert out == {"likes": 1, "dislikes": 0, "my_rating": {"liked": True, "difficulty": "right"}}
    raters[0].put(f"/api/problems/{slug}/rating", json={"liked": False, "difficulty": "harder"})  # changing your mind
    raters[1].put(f"/api/problems/{slug}/rating", json={"liked": False, "difficulty": "harder"})
    raters[2].put(f"/api/problems/{slug}/rating", json={"liked": True, "difficulty": None})
    assert raters[0].put(f"/api/problems/{slug}/rating", json={"difficulty": "impossible"}).status_code == 422

    detail = raters[1].get(f"/api/problems/{slug}").json()
    assert (detail["likes"], detail["dislikes"]) == (1, 2) and detail["my_rating"]["difficulty"] == "harder"
    listed = next(p for p in raters[1].get("/api/problems").json() if p["slug"] == slug)
    assert listed["like_pct"] == 33 and listed["ratings"] == 3

    admin = make_client("feedback_admin", admin=True)
    flagged = next(f for f in admin.get("/api/admin/problem-feedback").json() if f["slug"] == slug)
    assert flagged["flags"] == ["Mostly disliked", "Harder than 'easy'"]
    assert raters[0].get("/api/admin/problem-feedback").status_code == 403


def test_problem_detail_has_neighbours(client):
    first = client.get(f"/api/problems/{SLUG}").json()
    assert first["prev_slug"] is None and first["next_slug"] == "exact-arithmetic"
    assert client.get("/api/problems/exact-arithmetic").json()["prev_slug"] == SLUG


# ---------- C6: badges ----------

def test_badges_are_awarded_and_shown(make_client, fake_llm):
    ace = make_client("badge_ace")
    out = solve(ace)
    assert {b["key"] for b in out["new_badges"]} == {"first_solve", "one_shot"}
    assert solve(ace)["new_badges"] == []  # only announced once

    # Opening a hint first means no One-Shot; a failed first try doesn't either.
    hinted = make_client("badge_hinted")
    hinted.post(f"/api/problems/{SLUG}/hints/1")
    assert {b["key"] for b in solve(hinted)["new_badges"]} == {"first_solve"}

    profile = make_client().get("/api/users/badge_ace").json()
    by_key = {b["key"]: b for b in profile["badges"]}
    assert len(profile["badges"]) == len(badges.BADGES)
    assert by_key["one_shot"]["earned_at"] and by_key["solved_100"]["earned_at"] is None


def test_minimalist_streak_and_helper_badges(make_client, fake_llm):
    slow, lean = make_client("badge_slow"), make_client("badge_lean")
    solve(slow, "SOLVE LONG", slug="exact-arithmetic")
    out = solve(lean, "SOLVE TINY", slug="exact-arithmetic")
    assert "minimalist" in {b["key"] for b in out["new_badges"]}

    with SessionLocal() as db:
        db.execute(update(User).where(User.username == "badge_slow").values(best_streak=7))
        db.commit()
        user = db.scalar(select(User).where(User.username == "badge_slow"))
        assert {b["key"] for b in badges.evaluate(db, user)} == {"streak_3", "streak_7"}

    # Helper: a shared solution collects enough upvotes.
    sub_id = solve(slow, "SOLVE LONG again", slug="exact-arithmetic")["submission_id"]
    slow.post(f"/api/submissions/{sub_id}/share", json={"shared": True})
    for i in range(badges.HELPER_VOTES):
        ratelimit.reset()  # signups are limited per IP
        voter = make_client(f"badge_voter_{i}")
        solve(voter, "SOLVE LONG v", slug="exact-arithmetic")
        assert voter.post(f"/api/solutions/{sub_id}/vote").status_code == 200
    earned = {b["key"] for b in make_client().get("/api/users/badge_slow").json()["badges"] if b["earned_at"]}
    assert "helper" in earned


# ---------- C7: streak reminders ----------

DAY = date(2032, 5, 5)


@pytest.fixture
def reminder_day(monkeypatch):
    """A day with a daily problem, at 19:00 UTC (after the reminder hour)."""
    clock = {"t": datetime(DAY.year, DAY.month, DAY.day, 19, tzinfo=timezone.utc)}
    monkeypatch.setattr(daily, "now", lambda: clock["t"])
    with SessionLocal() as db:
        problem = db.scalar(select(Problem).where(Problem.slug == "secret-keeper"))
        db.add(DailyChallenge(day=DAY, problem_id=problem.id, source="queue"))
        db.execute(update(User).values(last_daily_solved=None, current_streak=0))
        db.commit()
    yield clock
    with SessionLocal() as db:
        db.query(DailyChallenge).filter(DailyChallenge.day == DAY).delete()
        db.commit()


def set_streak(username, streak, last_solved, **extra):
    with SessionLocal() as db:
        db.execute(update(User).where(User.username == username).values(
            current_streak=streak, best_streak=streak, last_daily_solved=last_solved, **extra))
        db.commit()


def test_streak_reminders(make_client, reminder_day):
    yesterday = DAY - timedelta(days=1)
    for name, verified in (("rem_long", True), ("rem_short", True), ("rem_done", True), ("rem_off", True),
                           ("rem_broken", True), ("rem_unverified", False)):
        ratelimit.reset()  # signups are limited per IP
        make_client(name, verified=verified)
    set_streak("rem_long", 9, yesterday)
    set_streak("rem_short", 2, yesterday)
    set_streak("rem_done", 5, DAY)  # already solved today
    set_streak("rem_off", 4, yesterday, reminder_emails=False)
    set_streak("rem_broken", 6, DAY - timedelta(days=3))  # streak already lost
    set_streak("rem_unverified", 3, yesterday)

    reminder_day["t"] = reminder_day["t"].replace(hour=10)
    assert reminders.send_due() == 0  # too early
    reminder_day["t"] = reminder_day["t"].replace(hour=19)

    emails.outbox.clear()
    object.__setattr__(settings, "reminder_daily_cap", 1)
    try:
        assert reminders.send_due() == 1  # the cap bites: the longest streak goes first
        assert emails.outbox[-1].to == "rem_long@example.com"
        object.__setattr__(settings, "reminder_daily_cap", 90)
        assert reminders.send_due() == 1
    finally:
        object.__setattr__(settings, "reminder_daily_cap", 90)
    assert [m.to for m in emails.outbox] == ["rem_long@example.com", "rem_short@example.com"]
    assert reminders.send_due() == 0  # never twice in a day

    message = emails.outbox[0]
    assert "9-day streak ends in 5 hours" in message.subject and "/problems/secret-keeper" in message.text
    assert "List-Unsubscribe" in message.headers and "/unsubscribe?token=" in message.text


def test_unsubscribe_link_and_settings_toggle(make_client):
    user = make_client("rem_toggle")
    uid = user_row("rem_toggle").id
    anon = make_client()
    assert anon.post("/api/auth/unsubscribe", params={"token": f"{uid}.forged"}).status_code == 400
    assert anon.post("/api/auth/unsubscribe", params={"token": reminders.unsubscribe_token(uid)}).status_code == 200
    assert user.get("/api/auth/me").json()["reminder_emails"] is False
    assert user.patch("/api/auth/me/preferences", json={"reminder_emails": True}).json()["reminder_emails"] is True
