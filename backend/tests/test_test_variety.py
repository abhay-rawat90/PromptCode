"""Starter problems gain new tests on existing databases; daily problems get a varied test count."""

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app import daily, llm
from app.db import SessionLocal
from app.models import Problem, TestCase
from app.schemas import ProblemIn
from app.seed import PROBLEMS, seed


def _tests(db, slug):
    problem = db.scalar(select(Problem).where(Problem.slug == slug))
    db.refresh(problem)
    return problem, sorted(problem.test_cases, key=lambda t: t.position)


def test_starter_problems_have_varied_test_counts():
    counts = {p["slug"]: len(p["tests"]) for p in PROBLEMS}
    assert len(set(counts.values())) >= 4 and min(counts.values()) >= 5 and max(counts.values()) >= 9


def test_existing_database_gets_the_new_starter_tests_appended():
    spec = next(p for p in PROBLEMS if p["slug"] == "exact-arithmetic")
    with SessionLocal() as db:
        problem, tests = _tests(db, "exact-arithmetic")
        kept_ids = [t.id for t in tests[:5]]
        for t in tests[5:]:  # an older database only had the first 5 tests
            problem.test_cases.remove(t)
        db.commit()
        assert len(_tests(db, "exact-arithmetic")[1]) == 5

        seed(db)  # runs at every startup
        _, tests = _tests(db, "exact-arithmetic")
        assert len(tests) == len(spec["tests"])
        assert [t.position for t in tests] == list(range(1, len(spec["tests"]) + 1))
        assert [t.id for t in tests[:5]] == kept_ids  # existing tests (and their results) untouched
        assert [t.input for t in tests] == [t["input"] for t in spec["tests"]]


def test_a_starter_problem_someone_edited_is_left_alone():
    with SessionLocal() as db:
        problem, tests = _tests(db, "contact-extractor")
        original = tests[0].expected
        for t in tests[3:]:
            problem.test_cases.remove(t)
        tests[0].expected = '{"type": "object"}'  # an admin changed a test
        db.commit()
        try:
            seed(db)
            _, after = _tests(db, "contact-extractor")
            assert len(after) == 3  # not a prefix of the built-in tests any more: nothing appended
        finally:  # put the problem back for other tests
            after[0].expected = original
            db.commit()
            seed(db)
            assert len(_tests(db, "contact-extractor")[1]) == 6


def test_daily_test_counts_follow_difficulty_and_vary():
    start = date(2031, 1, 6)  # a Monday
    plans = {}
    for i in range(70):
        day = start + timedelta(days=i)
        total, samples = daily.test_plan(day)
        low, high = daily.TEST_COUNT_RANGE[daily.DIFFICULTY_BY_WEEKDAY[day.weekday()]]
        assert low <= total <= high and samples == (3 if total >= 9 else 2)
        plans.setdefault(daily.DIFFICULTY_BY_WEEKDAY[day.weekday()], set()).add(total)
    assert all(len(totals) >= 2 for totals in plans.values())  # not the same count every time


def test_brief_asks_for_the_days_test_count():
    day = date(2031, 1, 9)  # a Thursday (hard)
    total, samples = daily.test_plan(day)
    brief = daily._brief(day, None)
    assert f"Test cases: exactly {total}, of which {samples} are visible samples" in brief


def _spec(n_tests: int, samples: int) -> ProblemIn:
    return ProblemIn(
        title="Count Check", description="d", difficulty="easy",
        test_cases=[{"input": f"w{i}", "check_type": "exact", "expected": f"W{i}", "is_sample": i < samples}
                    for i in range(n_tests)],
    )


def test_generated_problems_must_have_5_to_12_tests(monkeypatch):
    async def never_called(prompt, test_input):
        raise AssertionError("size is checked before running anything")

    monkeypatch.setattr(llm, "run_prompt", never_called)
    for n_tests, samples in ((4, 2), (13, 3), (8, 4), (5, 3)):  # too few, too many, 4 samples, only 2 hidden
        problem = asyncio.run(daily.verify(_spec(n_tests, samples), "SHOUT"))
        assert problem and problem.startswith("Need 5-12 tests"), (n_tests, samples)
