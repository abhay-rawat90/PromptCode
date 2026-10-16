import pytest

from app import llm


@pytest.fixture
def fake_llm(monkeypatch):
    """Model that answers 'positive' only when the prompt says so; judge always passes."""

    async def run_prompt(prompt, test_input):
        text = "positive" if "ALWAYS_POSITIVE" in prompt else "nope"
        return llm.LLMResult(text=text, input_tokens=10, output_tokens=2)

    async def judge(rubric, test_input, output):
        return True, "Looks good.", llm.LLMResult(input_tokens=5, output_tokens=5)

    monkeypatch.setattr(llm, "run_prompt", run_prompt)
    monkeypatch.setattr(llm, "judge", judge)


PROBLEM = {
    "title": "Always Positive",
    "description": "Make the model say positive.",
    "difficulty": "easy",
    "tags": ["Test", "demo"],
    "test_cases": [
        {"input": "a", "check_type": "exact", "expected": "positive", "is_sample": True},
        {"input": "b", "check_type": "exact", "expected": "positive"},
        {"input": "c", "check_type": "llm_judge", "expected": "Says positive."},
    ],
}


def test_seeded_problems_listed(client):
    r = client.get("/api/problems")
    assert r.status_code == 200
    assert any(p["slug"] == "sentiment-label" for p in r.json())


def test_problem_search_treats_wildcards_literally(client):
    assert client.get("/api/problems", params={"q": "%"}).json() == []
    assert len(client.get("/api/problems", params={"q": "sentiment"}).json()) == 1


def test_problem_lifecycle_and_submit(make_client, fake_llm):
    author = make_client("author")
    solver = make_client("solver")
    anon = make_client()

    r = author.post("/api/problems", json=PROBLEM)
    assert r.status_code == 201, r.text
    detail = r.json()
    slug = detail["slug"]
    assert detail["hidden_count"] == 2 and len(detail["sample_tests"]) == 1
    assert detail["tags"] == ["test", "demo"]

    # Only the author can see hidden tests or edit.
    assert solver.get(f"/api/problems/{slug}/full").status_code == 403
    assert len(author.get(f"/api/problems/{slug}/full").json()["test_cases"]) == 3

    # Run requires auth and only uses sample tests.
    assert anon.post(f"/api/problems/{slug}/run", json={"prompt": "x"}).status_code == 401
    run = solver.post(f"/api/problems/{slug}/run", json={"prompt": "x"}).json()
    assert run["total"] == 1 and run["status"] == "wrong_answer"
    assert run["daily_remaining"] is not None

    # Failing submit: hidden tests reveal nothing.
    sub = solver.post(f"/api/problems/{slug}/submit", json={"prompt": "x"}).json()
    assert sub["status"] == "wrong_answer" and sub["passed"] == 1  # judge passes
    hidden = [res for res in sub["results"] if not res["is_sample"]]
    assert all(res["input"] is None and res["output"] is None for res in hidden)

    # Passing submit.
    sub = solver.post(f"/api/problems/{slug}/submit", json={"prompt": "ALWAYS_POSITIVE"}).json()
    assert sub["status"] == "accepted" and sub["passed"] == 3

    listed = {p["slug"]: p for p in solver.get("/api/problems").json()}
    assert listed[slug]["solved"] and listed[slug]["acceptance"] == 50.0

    history = solver.get(f"/api/submissions?problem={slug}").json()
    assert len(history) == 2
    assert author.get(f"/api/submissions/{history[0]['id']}").status_code == 404

    profile = anon.get("/api/users/SOLVER").json()  # case-insensitive lookup
    assert profile["solved"] == 1 and profile["solved_by_difficulty"]["easy"] == 1
    assert anon.get("/api/users/author").json()["problems_authored"] == 1

    board = anon.get("/api/leaderboard").json()
    assert board[0]["username"] == "solver"

    assert solver.delete(f"/api/problems/{slug}").status_code == 403
    assert author.delete(f"/api/problems/{slug}").status_code == 204


def test_daily_budget_and_burst_limits(make_client, fake_llm, monkeypatch):
    from app import ratelimit
    from limits import parse

    monkeypatch.setitem(ratelimit.LIMITS, "daily_tests_user", parse("3/day"))
    user = make_client("budgeteer")
    # sentiment-label has 2 sample tests: the first run spends 2 of 3, the second needs 2 more.
    assert user.post("/api/problems/sentiment-label/run", json={"prompt": "x"}).status_code == 200
    r = user.post("/api/problems/sentiment-label/run", json={"prompt": "x"})
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0


def test_validation_rejects_bad_problems(make_client):
    c = make_client("validator")
    no_sample = {**PROBLEM, "test_cases": [{"input": "a", "check_type": "exact", "expected": "x"}]}
    r = c.post("/api/problems", json=no_sample)
    assert r.status_code == 422 and r.json()["detail"][0]["msg"].startswith("At least one")
    bad_regex = {**PROBLEM, "test_cases": [{"input": "a", "check_type": "regex", "expected": "(", "is_sample": True}]}
    assert c.post("/api/problems", json=bad_regex).status_code == 422
