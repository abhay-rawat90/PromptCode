"""Phase A: pacing, fallback models, caching, early stop, live progress and users' own keys."""

import asyncio
import json
import types as pytypes

import pytest
from sqlalchemy import func, select, update

from app import llm, quota
from app.config import settings
from app.db import SessionLocal
from app.models import LLMCache, ModelUsageDaily, User

MAIN = "gemini:gemini-3.5-flash-lite"
BACKUP = "groq:openai/gpt-oss-20b"


def _set(name, value):
    object.__setattr__(settings, name, value)


@pytest.fixture
def clock(monkeypatch):
    """A fake clock for the pacer: sleeping advances it instantly."""
    now = {"mono": 1000.0, "wall": 1_800_000_000.0}
    fake_time = pytypes.SimpleNamespace(monotonic=lambda: now["mono"], time=lambda: now["wall"])
    monkeypatch.setattr(quota, "time", fake_time)
    monkeypatch.setattr(llm, "time", fake_time)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(round(seconds, 1))
        now["mono"] += seconds
        now["wall"] += seconds

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    now["sleeps"] = sleeps
    return now


@pytest.fixture
def models(monkeypatch):
    """Both providers 'configured', with scripted answers/errors per model and a call log."""
    saved = {n: getattr(settings, n) for n in ("gemini_api_key", "groq_api_key", "gemini_model",
                                                "run_fallback_models", "llm_cache_days")}
    _set("gemini_api_key", "test-gemini")
    _set("groq_api_key", "test-groq")
    _set("gemini_model", "gemini-3.5-flash-lite")
    _set("run_fallback_models", [BACKUP])
    _set("llm_cache_days", 30)
    with SessionLocal() as db:  # each test starts with an empty cache
        db.query(LLMCache).delete()
        db.commit()

    script: dict[str, list] = {MAIN: [], BACKUP: []}
    calls: list[str] = []

    async def fake_call(ref, req, state):
        calls.append(ref.label)
        queued = script.get(ref.label) or []
        item = queued.pop(0) if queued else "ok"
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else "ok"
        return llm.Completion(text=f"{text}", input_tokens=10, output_tokens=2, model=ref.label)

    monkeypatch.setattr(llm, "_call", fake_call)
    yield pytypes.SimpleNamespace(script=script, calls=calls)
    for name, value in saved.items():
        _set(name, value)


def run(prompt="Say hi", test_input="x"):
    return asyncio.run(llm.run_prompt(prompt, test_input))


# ---------- pacing ----------

def test_pacer_queues_requests_beyond_the_per_minute_limit(clock):
    st = quota.ModelState("gemini:m", rpm=2, tpm=0, rpd=0)
    assert st.delay(10) == 0
    st.reserve(10)
    clock["mono"] += 5
    st.reserve(10)
    assert st.delay(10) == pytest.approx(55)  # the first request leaves the window after 60s
    clock["mono"] += 55
    assert st.delay(10) == 0


def test_pacer_respects_tokens_per_minute(clock):
    st = quota.ModelState("groq:m", rpm=100, tpm=1000, rpd=0)
    st.settle(st.reserve(900), 900)
    assert st.delay(50) == 0
    assert st.delay(200) == pytest.approx(60)


def test_model_limits_can_be_overridden(monkeypatch):
    _set("model_limits", "gemini:gemini-3.5-flash-lite=15:0:1000, bad-entry")
    try:
        st = quota.state(MAIN)
        assert (st.rpm, st.tpm, st.rpd) == (15, 0, 1000)
    finally:
        _set("model_limits", "")


def test_groq_headers_update_real_limits():
    st = quota.ModelState(BACKUP, rpm=30, tpm=1, rpd=1)
    st.update_from_groq_headers({"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "998",
                                 "x-ratelimit-limit-tokens": "8000"})
    assert (st.rpd, st.remaining_today, st.tpm) == (1000, 998, 8000)


def test_duration_parsing():
    assert quota.parse_duration("1m26.4s") == pytest.approx(86.4)
    assert quota.parse_duration("945ms") == pytest.approx(0.945)
    assert quota.parse_duration("2") == 2
    assert quota.parse_duration("") is None


# ---------- fallback ----------

def test_short_rate_limit_and_overload_are_waited_out_on_the_main_model(models, clock):
    models.script[MAIN] = [llm.ProviderError(429, "slow down", 3.0), llm.ProviderError(503, "overloaded")]
    result = run()
    assert result.error is None and not result.backup and result.model == MAIN
    assert models.calls == [MAIN, MAIN, MAIN]
    assert clock["sleeps"] == [3.0, 2.0]


def test_exhausted_main_model_falls_back_and_is_then_skipped(models, clock):
    models.script[MAIN] = [llm.ProviderError(429, "quota", 50_000)]
    first = run("one")
    assert first.backup and first.model == BACKUP and first.error is None
    second = run("two")
    assert second.backup
    assert models.calls == [MAIN, BACKUP, BACKUP]  # the blocked main model wasn't called again


def test_busy_main_model_hands_over_to_backup_quickly(models, clock):
    st = quota.state(MAIN)
    for _ in range(st.rpm):
        st.reserve(10)  # the main model's per-minute allowance is used up for ~60s
    result = run()
    assert result.backup and models.calls == [BACKUP]


def test_without_backups_a_busy_model_is_waited_for(models, clock):
    _set("run_fallback_models", [])
    st = quota.state(MAIN)
    for _ in range(st.rpm):
        st.reserve(10)
    assert "rate limit" in run().error  # longer than a user request may wait (45s)...

    async def patient():
        with llm.patience(120):  # ...but a background job waits its turn
            return await llm.run_prompt("Say hi", "x")

    result = asyncio.run(patient())
    assert not result.backup and result.error is None and models.calls == [MAIN]
    assert clock["sleeps"] and clock["sleeps"][0] == pytest.approx(60)


def test_everything_exhausted_reports_quota(models, clock):
    models.script[MAIN] = [llm.ProviderError(429, "quota", 50_000)]
    models.script[BACKUP] = [llm.ProviderError(429, "quota", 50_000)]
    result = run()
    assert result.quota_exhausted and "quota" in result.error


def test_bad_request_does_not_fall_back(models, clock):
    models.script[MAIN] = [llm.ProviderError(400, "prompt too long")]
    result = run()
    assert result.error and "prompt too long" in result.error and models.calls == [MAIN]


def test_primary_only_never_uses_backups(models, clock):
    models.script[MAIN] = [llm.ProviderError(429, "quota", 50_000)]

    async def go():
        with llm.primary_only():
            return await llm.run_prompt("p", "x")

    result = asyncio.run(go())
    assert result.error and models.calls == [MAIN]


# ---------- cache ----------

def test_identical_requests_are_served_from_the_cache(models, clock):
    first, second = run("same prompt"), run("same prompt")
    assert not first.cached and second.cached and second.text == first.text
    assert models.calls == [MAIN]
    run("different prompt")
    assert models.calls == [MAIN, MAIN]
    with SessionLocal() as db:
        row = db.scalar(select(ModelUsageDaily).where(ModelUsageDaily.model == MAIN))
        assert row.cache_hits >= 1 and row.requests >= 2


def test_cache_can_be_disabled(models, clock):
    _set("llm_cache_days", 0)
    run("p"), run("p")
    assert models.calls == [MAIN, MAIN]


def test_concurrent_identical_requests_share_one_call(models, clock):
    async def go():
        return await asyncio.gather(*(llm.run_prompt("dup", "x") for _ in range(3)))

    results = asyncio.run(go())
    assert models.calls == [MAIN] and all(r.text == "ok" for r in results)


def test_judge_verdicts_are_cached_and_parsed(models, clock):
    models.script[MAIN] = []
    _set("judge_model", "gemini-3.5-flash-lite")

    async def fake_call(ref, req, state):
        models.calls.append(ref.label)
        text = json.dumps({"reasoning": "fine", "passed": True})
        return llm.Completion(text=text, parsed=llm.Verdict.model_validate_json(text), model=ref.label)

    llm_call = llm._call
    try:
        llm._call = fake_call
        a = asyncio.run(llm.judge("rubric", "in", "out"))
        b = asyncio.run(llm.judge("rubric", "in", "out"))
    finally:
        llm._call = llm_call
    assert a[0] is True and b[0] is True and b[2].cached and len(models.calls) == 1


# ---------- grading: early stop, budget, streaming ----------

PROBLEM = {
    "title": "Early Stop Demo",
    "description": "Say ok.",
    "difficulty": "easy",
    "tags": [],
    "test_cases": [
        {"input": "1", "check_type": "exact", "expected": "ok", "is_sample": True},
        *({"input": str(i), "check_type": "exact", "expected": "ok"} for i in range(2, 8)),
    ],
}


@pytest.fixture
def fake_runs(monkeypatch):
    calls = []

    async def run_prompt(prompt, test_input):
        calls.append(test_input)
        llm.emit({"type": "waiting", "model": "test", "seconds": 1, "queue": 1})
        return llm.LLMResult(text="ok" if "GOOD" in prompt else "nope", input_tokens=5, output_tokens=1, model=MAIN)

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    return calls


def test_submit_stops_at_first_failure_and_charges_only_real_calls(make_client, fake_runs):
    author = make_client("stopper")
    slug = author.post("/api/problems", json=PROBLEM).json()["slug"]
    before = author.post(f"/api/problems/{slug}/run", json={"prompt": "GOOD"}).json()["daily_remaining"]
    fake_runs.clear()

    out = author.post(f"/api/problems/{slug}/submit", json={"prompt": "bad"}).json()
    assert out["status"] == "wrong_answer" and out["total"] == 7
    assert out["skipped"] == 7 - len(fake_runs) and len(fake_runs) <= settings.max_concurrency
    assert out["results"][0]["passed"] is False and out["results"][-1]["skipped"] is True
    assert out["daily_remaining"] == before - len(fake_runs)

    fake_runs.clear()
    good = author.post(f"/api/problems/{slug}/submit", json={"prompt": "GOOD"}).json()
    assert good["status"] == "accepted" and good["skipped"] == 0 and len(fake_runs) == 7


def test_run_and_submit_can_stream_progress(make_client, fake_runs):
    user = make_client("streamer")
    slug = user.post("/api/problems", json={**PROBLEM, "title": "Stream Demo"}).json()["slug"]
    r = user.post(f"/api/problems/{slug}/run", json={"prompt": "GOOD"},
                  headers={"Accept": "application/x-ndjson"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines()]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "result"
    assert "progress" in kinds and "waiting" in kinds
    assert events[-1]["data"]["status"] == "accepted"

    sub = user.post(f"/api/problems/{slug}/submit", json={"prompt": "GOOD"},
                    headers={"Accept": "application/x-ndjson"})
    result = json.loads(sub.text.splitlines()[-1])["data"]
    assert result["submission_id"] and result["status"] == "accepted"


def test_streaming_reports_budget_errors_as_plain_http(make_client, fake_runs, monkeypatch):
    from limits import parse

    from app import ratelimit

    monkeypatch.setitem(ratelimit.LIMITS, "daily_tests_user", parse("1/day"))
    user = make_client("streamlimited")
    r = user.post("/api/problems/sentiment-label/run", json={"prompt": "GOOD"},
                  headers={"Accept": "application/x-ndjson"})
    assert r.status_code == 429 and "budget" in r.json()["detail"]


# ---------- users' own keys ----------

def test_own_gemini_key_is_validated_encrypted_and_used(make_client, monkeypatch):
    checked = []

    async def check(key):
        checked.append(key)
        return None if key.startswith("AIza") else "Gemini rejected this API key."

    monkeypatch.setattr(llm, "check_api_key", check)
    user = make_client("keyholder")
    key = "AIza" + "x" * 35

    assert user.put("/api/auth/me/gemini-key", json={"api_key": "bad key!"}).status_code == 422
    assert user.put("/api/auth/me/gemini-key", json={"api_key": "B" * 39}).status_code == 400
    me = user.put("/api/auth/me/gemini-key", json={"api_key": key}).json()
    assert me["has_own_key"] and me["own_key_hint"] == key[-4:]
    with SessionLocal() as db:
        stored = db.scalar(select(User.gemini_key_enc).where(func.lower(User.username) == "keyholder"))
    assert stored and key not in stored

    used_keys = []

    async def fake_gemini(ref, req, api_key=None):
        used_keys.append(api_key)
        return llm.Completion(text="positive", input_tokens=3, output_tokens=1, model=ref.label)

    monkeypatch.setattr(llm, "_call_gemini", fake_gemini)
    out = user.post("/api/problems/sentiment-label/run", json={"prompt": "Own key {{input}}"}).json()
    assert out["own_key"] is True and out["daily_remaining"] is None
    assert used_keys and all(k == key for k in used_keys)

    me = user.delete("/api/auth/me/gemini-key").json()
    assert not me["has_own_key"] and me["own_key_hint"] is None


def test_own_key_errors_are_explained(monkeypatch):
    async def rejected(ref, req, api_key=None):
        raise llm.ProviderError(400, "API key not valid. Please pass a valid API key.")

    monkeypatch.setattr(llm, "_call_gemini", rejected)

    async def go():
        with llm.own_key("AIza-test"):
            return await llm.run_prompt("unique own key prompt", "x")

    assert "Your Gemini API key was rejected" in asyncio.run(go()).error


# ---------- admin ----------

def test_admin_sees_model_quotas_and_warnings(make_client):
    admin = make_client("quotaadmin")
    with SessionLocal() as db:
        db.execute(update(User).where(func.lower(User.username) == "quotaadmin").values(is_admin=True))
        db.commit()
    report = admin.get("/api/admin/usage").json()
    labels = {m["model"] for m in report["models"]}
    assert f"gemini:{settings.gemini_model}" in labels
    # Tests run without API keys, so the report must say so.
    assert any("no API key" in w for w in report["warnings"])
    assert all(m["status"] == "no_key" for m in report["models"])
