"""Phase B: user-created problems switched off, verified problems, reports and moderation."""

import json

import pytest
from sqlalchemy import func, select

from app import llm
from app.config import settings
from app.db import SessionLocal
from app.models import Problem, TestCase, User


@pytest.fixture
def fake_llm(monkeypatch):
    """Answers 'yes' only for prompts containing SOLVE; the judge passes everything."""

    async def run_prompt(prompt, test_input):
        return llm.LLMResult(text="yes" if "SOLVE" in prompt else f"echo {test_input}", input_tokens=4, output_tokens=1,
                             model="gemini:gemini-3.5-flash-lite")

    async def judge(rubric, test_input, output):
        return True, "ok", llm.LLMResult()

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    monkeypatch.setattr(llm, "judge", judge)


def problem(title="Say Yes", reference="SOLVE it", **extra):
    return {
        "title": title,
        "description": "Answer yes.",
        "difficulty": "easy",
        "tags": [],
        "reference_prompt": reference,
        "test_cases": [
            {"input": "a", "check_type": "exact", "expected": "yes", "is_sample": True},
            {"input": "b", "check_type": "exact", "expected": "yes"},
        ],
        **extra,
    }


def _user_problem(username: str, title: str) -> str:
    """A problem made by a non-admin (e.g. before user problems were turned off)."""
    with SessionLocal() as db:
        author = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        slug = title.lower().replace(" ", "-")
        p = Problem(slug=slug, title=title, description="d", difficulty="easy", tags="", author_id=author.id)
        p.test_cases = [TestCase(position=1, input="", check_type="contains", expected="x", is_sample=True)]
        db.add(p)
        db.commit()
    return slug


# ---------- B1: user-created problems are off ----------

def test_only_admins_can_create_problems(make_client, fake_llm):
    assert make_client("regular_joe").post("/api/problems", json=problem()).status_code == 403
    assert make_client("regular_joe2").post("/api/problems/verify", json=problem()).status_code == 403
    admin = make_client("boss", admin=True)
    assert admin.post("/api/problems", json=problem(title="Admin Yes")).status_code == 201
    assert make_client().get("/api/auth/config").json()["allow_user_problems"] is False


def test_user_problems_are_hidden_everywhere_but_kept(make_client, fake_llm):
    author = make_client("old_author")
    slug = _user_problem("old_author", "Legacy Community Problem")
    anon = make_client()

    assert slug not in {p["slug"] for p in anon.get("/api/problems").json()}
    assert anon.get(f"/api/problems/{slug}").status_code == 404
    assert author.post(f"/api/problems/{slug}/run", json={"prompt": "x"}).status_code == 404
    assert anon.get("/api/users/old_author").json()["problems_authored"] == 0

    object.__setattr__(settings, "allow_user_problems", True)  # switching it back on restores them
    try:
        assert anon.get(f"/api/problems/{slug}").status_code == 200
        assert anon.get("/api/users/old_author").json()["problems_authored"] == 1
    finally:
        object.__setattr__(settings, "allow_user_problems", False)


# ---------- B2: verification ----------

def test_verify_shows_the_author_every_test(make_client, fake_llm):
    admin = make_client("verifier", admin=True)
    good = admin.post("/api/problems/verify", json=problem()).json()
    assert good["ok"] is True and good["problems"] == [] and good["passed"] == 2
    hidden = [r for r in good["results"] if not r["is_sample"]][0]
    assert hidden["input"] == "b" and hidden["output"] == "yes"  # the author sees hidden tests

    bad = admin.post("/api/problems/verify", json=problem(reference="wrong")).json()
    assert bad["ok"] is False and len(bad["problems"]) == 2 and bad["problems"][0].startswith("Test 1")

    trivial = problem(reference="SOLVE")
    trivial["test_cases"] = [{"input": "yes", "check_type": "contains", "expected": "yes", "is_sample": True}]
    out = admin.post("/api/problems/verify", json=trivial).json()
    assert out["ok"] is False and "repeats the input" in out["problems"][0]

    assert admin.post("/api/problems/verify", json=problem(reference="")).status_code == 422


def test_verify_streams_progress(make_client, fake_llm):
    admin = make_client("streamverifier", admin=True)
    r = admin.post("/api/problems/verify", json=problem(), headers={"Accept": "application/x-ndjson"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[0]["type"] == "start" and events[-1]["type"] == "result" and events[-1]["data"]["ok"] is True


def test_publishing_and_editing_require_passing_verification(make_client, fake_llm):
    admin = make_client("publisher", admin=True)
    r = admin.post("/api/problems", json=problem(title="Broken One", reference="wrong"))
    assert r.status_code == 422 and "didn't pass verification" in r.json()["detail"]
    assert admin.post("/api/problems", json=problem(title="No Ref", reference="")).status_code == 422

    created = admin.post("/api/problems", json=problem(title="Good One"))
    assert created.status_code == 201 and created.json()["verified"] is True
    slug = created.json()["slug"]
    assert admin.get(f"/api/problems/{slug}/full").json()["reference_prompt"] == "SOLVE it"

    assert admin.put(f"/api/problems/{slug}", json=problem(title="Good One", reference="wrong")).status_code == 422
    assert admin.put(f"/api/problems/{slug}", json=problem(title="Good One v2")).status_code == 200


# ---------- B3: reports and moderation ----------

def test_report_hide_and_restore(make_client, fake_llm):
    admin = make_client("mod_person", admin=True)
    slug = admin.post("/api/problems", json=problem(title="Reported Problem")).json()["slug"]
    pid = admin.get(f"/api/problems/{slug}").json()["id"]
    reporter = make_client("reporter")

    assert make_client("unverified_reporter", verified=False).post(
        f"/api/problems/{slug}/report", json={"reason": "wrong_tests"}).status_code == 403
    assert reporter.post(f"/api/problems/{slug}/report", json={"reason": "nonsense"}).status_code == 422
    assert reporter.post(f"/api/problems/{slug}/report", json={"reason": "wrong_tests", "note": "Test 2 is wrong"}).status_code == 200
    # Reporting again updates the same report instead of piling up.
    assert reporter.post(f"/api/problems/{slug}/report", json={"reason": "unclear", "note": "Actually unclear"}).status_code == 200

    assert reporter.get("/api/admin/reports").status_code == 403
    queue = admin.get("/api/admin/reports").json()
    group = next(g for g in queue if g["slug"] == slug)
    assert group["open_reports"] == 1 and group["reports"][0]["reason"] == "unclear"
    assert admin.get("/api/admin/usage").json()["open_reports"] >= 1

    # Dismiss: reports close, problem stays up.
    assert admin.post(f"/api/admin/problems/{pid}/dismiss-reports").status_code == 204
    assert all(g["slug"] != slug for g in admin.get("/api/admin/reports").json())

    # Hide: gone for users, still reviewable by admins.
    reporter.post(f"/api/problems/{slug}/report", json={"reason": "wrong_tests"})
    assert admin.post(f"/api/admin/problems/{pid}/hide", json={"reason": "Broken tests"}).status_code == 204
    assert reporter.get(f"/api/problems/{slug}").status_code == 404
    assert slug not in {p["slug"] for p in reporter.get("/api/problems").json()}
    assert admin.get(f"/api/problems/{slug}").json()["hidden_reason"] == "Broken tests"
    group = next(g for g in admin.get("/api/admin/reports").json() if g["slug"] == slug)
    assert group["hidden"] and group["open_reports"] == 0

    assert admin.post(f"/api/admin/problems/{pid}/unhide").status_code == 204
    assert reporter.get(f"/api/problems/{slug}").status_code == 200
    assert admin.post("/api/admin/problems/999999/hide", json={}).status_code == 404
