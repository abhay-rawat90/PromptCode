import json

from app.grader import check_deterministic, extract_json
from app.llm import build_request


def test_exact_ignores_case_and_whitespace():
    assert check_deterministic("exact", "positive", "  Positive\n")[0]
    assert not check_deterministic("exact", "positive", "Positive.")[0]
    assert not check_deterministic("exact", "positive", "Positive", case_sensitive=True)[0]


def test_contains_and_not_contains():
    assert check_deterministic("contains", "paris", "The capital is Paris.")[0]
    assert check_deterministic("not_contains", "BAKE-7731", "I can't share that.")[0]
    passed, detail = check_deterministic("not_contains", "BAKE-7731", "code: bake-7731")
    assert not passed and "BAKE" not in detail  # never leak the expected value


def test_regex():
    assert check_deterministic("regex", r"\A84\Z", "84\n")[0]
    assert not check_deterministic("regex", r"\A84\Z", "The answer is 84")[0]


def test_regex_timeout_does_not_hang():
    passed, detail = check_deterministic("regex", r"(a+)+$", "a" * 40 + "!")
    assert not passed


def test_json_schema_with_code_fence():
    schema = json.dumps({"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}})
    assert check_deterministic("json_schema", schema, '```json\n{"name": "x"}\n```')[0]
    passed, detail = check_deterministic("json_schema", schema, '{"name": 3}')
    assert not passed and "name" in detail
    passed, detail = check_deterministic("json_schema", schema, "not json")
    assert not passed and "not valid JSON" in detail


def test_extract_json_leaves_plain_json():
    assert extract_json(' {"a": 1} ') == '{"a": 1}'


def test_build_request_modes():
    assert build_request("Classify: {{input}}", "hi") == (None, "Classify: hi")
    assert build_request("Be a pirate.", "hello") == ("Be a pirate.", "hello")
    assert build_request("Tell a joke.", "") == (None, "Tell a joke.")


def test_gemini_calls_wait_out_rate_limits_and_overloads(monkeypatch):
    import asyncio

    from google.genai import errors

    from app import llm

    calls, sleeps = [], []
    failures = [
        errors.APIError(429, {"error": {"code": 429, "message": "Please retry in 2.5s."}}),
        errors.APIError(503, {"error": {"code": 503, "message": "overloaded"}}),
    ]

    class FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs["model"])
            if failures:
                raise failures.pop(0)
            return "response"

    class FakeClient:
        class aio:
            models = FakeModels()

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    assert asyncio.run(llm._generate("m", "hi", None)) == "response"
    assert len(calls) == 3 and sleeps == [3.5, 2.0]

    # A 400 is a real error and is not retried.
    failures.append(errors.APIError(400, {"error": {"code": 400, "message": "bad"}}))
    try:
        asyncio.run(llm._generate("m", "hi", None))
        raise AssertionError("expected APIError")
    except errors.APIError as exc:
        assert exc.code == 400


def test_retry_delay_parsing_per_minute_vs_per_day():
    from google.genai import errors

    from app.llm import _retry_after_seconds, is_quota_exhausted

    per_minute = errors.APIError(429, {"error": {"code": 429, "message": "Please retry in 30.6s."}})
    per_day = errors.APIError(429, {"error": {"code": 429, "message": "Please retry in 14h52m5.15s.",
                                               "details": [{"retryDelay": "53525s"}]}})
    text_only_day = errors.APIError(429, {"error": {"code": 429, "message": "Please retry in 2h3m."}})
    assert round(_retry_after_seconds(per_minute), 1) == 31.6 and not is_quota_exhausted(per_minute)
    assert _retry_after_seconds(per_day) == 53526 and is_quota_exhausted(per_day)
    assert _retry_after_seconds(text_only_day) == 2 * 3600 + 3 * 60 + 1 and is_quota_exhausted(text_only_day)
