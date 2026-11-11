"""Runs a prompt against test cases and grades each output."""

import asyncio
import json
from dataclasses import dataclass, field

import jsonschema
import regex

from . import llm
from .config import settings
from .models import TestCase

DETERMINISTIC_CHECKS = {"exact", "contains", "not_contains", "regex", "json_schema"}
REGEX_TIMEOUT_SECONDS = 1.0

# Caps in-flight model calls across all requests in this process, so a traffic spike queues
# instead of tripping the provider's rate limits.
_global_slots = asyncio.Semaphore(settings.global_llm_concurrency)


def _norm(text: str, case_sensitive: bool) -> str:
    text = text.strip()
    return text if case_sensitive else text.casefold()


extract_json = llm.extract_json


def check_deterministic(
    check_type: str, expected: str, output: str, case_sensitive: bool = False
) -> tuple[bool, str | None]:
    """Return (passed, detail). `detail` explains a failure and never reveals `expected`."""
    if check_type == "exact":
        ok = _norm(output, case_sensitive) == _norm(expected, case_sensitive)
        return ok, None if ok else "Output does not exactly match the expected answer."

    if check_type == "contains":
        ok = _norm(expected, case_sensitive) in _norm(output, case_sensitive)
        return ok, None if ok else "Output is missing required text."

    if check_type == "not_contains":
        ok = _norm(expected, case_sensitive) not in _norm(output, case_sensitive)
        return ok, None if ok else "Output contains forbidden text."

    if check_type == "regex":
        flags = 0 if case_sensitive else regex.IGNORECASE
        try:
            ok = regex.search(expected, output.strip(), flags, timeout=REGEX_TIMEOUT_SECONDS) is not None
        except TimeoutError:
            return False, "Pattern check timed out."
        return ok, None if ok else "Output does not match the required pattern."

    if check_type == "json_schema":
        try:
            data = json.loads(extract_json(output))
        except json.JSONDecodeError as exc:
            return False, f"Output is not valid JSON ({exc.msg})."
        try:
            jsonschema.validate(data, json.loads(expected))
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "root"
            return False, f"JSON does not match schema at {where}: {exc.message}"
        return True, None

    raise ValueError(f"Not a deterministic check: {check_type}")


SKIPPED_DETAIL = "Not run: an earlier test failed."


@dataclass
class CaseResult:
    test_id: int
    position: int
    is_sample: bool
    check_type: str
    passed: bool
    output: str
    detail: str | None
    error: bool = False
    skipped: bool = False  # not run because Submit stopped at an earlier failure
    model: str = ""  # model that produced the output ("provider:model")
    backup: bool = False  # output or verdict came from a backup model
    cached: bool = False  # output came from the cache
    executed: bool = False  # a real model call was spent on the output (counts toward budgets)

    def public(self, case: TestCase, reveal: bool = False) -> dict:
        """Serialized result. Hidden tests reveal only pass/fail (and infra errors), unless
        `reveal` (the problem's author verifying it)."""
        data = {
            "test_id": self.test_id,
            "position": self.position,
            "is_sample": self.is_sample,
            "check_type": self.check_type,
            "passed": self.passed,
            "error": self.error,
            "skipped": self.skipped,
            "input": None,
            "expected": None,
            "output": None,
            "detail": self.detail if (reveal or self.is_sample or self.error or self.skipped) else None,
            "model": llm.display_name(self.model) if self.model else None,
            "backup": self.backup,
            "cached": self.cached,
        }
        if self.is_sample or reveal:
            data.update(input=case.input, expected=case.expected, output=self.output)
        return data


@dataclass
class EvalResult:
    cases: list[CaseResult] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def executed(self) -> int:
        """Model runs actually spent (cache hits and skipped tests are free)."""
        return sum(c.executed for c in self.cases)

    @property
    def backup_models(self) -> list[str]:
        return sorted({llm.display_name(c.model) for c in self.cases if c.backup and c.model})

    @property
    def status(self) -> str:
        if self.passed == len(self.cases):
            return "accepted"
        if any(c.error for c in self.cases):
            return "error"
        return "wrong_answer"


async def _grade_case(prompt: str, case: TestCase, totals: EvalResult) -> CaseResult:
    run = await llm.run_prompt(prompt, case.input)
    totals.input_tokens += run.input_tokens
    totals.output_tokens += run.output_tokens
    meta = {"model": run.model, "backup": run.backup, "cached": run.cached,
            "executed": not run.cached and bool(run.model or not run.error)}

    def result(passed: bool, output: str, detail: str | None, error: bool = False) -> CaseResult:
        return CaseResult(case.id, case.position, case.is_sample, case.check_type, passed, output, detail, error, **meta)

    if run.error:
        # A truncated output is the prompt's fault, not an infrastructure error.
        is_infra = not run.error.startswith("Output was cut off")
        return result(False, run.text, run.error, error=is_infra)

    if case.check_type in DETERMINISTIC_CHECKS:
        passed, detail = check_deterministic(case.check_type, case.expected, run.text, case.case_sensitive)
        return result(passed, run.text, detail)

    if not run.text.strip():  # nothing to judge: don't spend a judge call
        return result(False, run.text, "The model returned an empty output.")
    passed, reasoning, usage = await llm.judge(case.expected, case.input, run.text)
    totals.input_tokens += usage.input_tokens
    totals.output_tokens += usage.output_tokens
    meta["backup"] = meta["backup"] or usage.backup
    if usage.error:
        return result(False, run.text, usage.error, error=True)
    return result(passed, run.text, reasoning or None)


def _skipped(case: TestCase) -> CaseResult:
    return CaseResult(case.id, case.position, case.is_sample, case.check_type, False, "", SKIPPED_DETAIL, skipped=True)


async def evaluate(prompt: str, cases: list[TestCase], *, stop_on_failure: bool = False) -> EvalResult:
    """Grade `prompt` on `cases`. With stop_on_failure (Submit), samples run first, then hidden
    tests in small batches, and grading stops after the batch with the first failure - the rest
    are reported as skipped. Most submissions fail, so this saves a lot of quota."""
    totals = EvalResult()
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    done = 0

    async def bounded(case: TestCase) -> CaseResult:
        nonlocal done
        async with semaphore, _global_slots:
            result = await _grade_case(prompt, case, totals)
        done += 1
        llm.emit({"type": "progress", "done": done, "total": len(cases), "passed": result.passed})
        return result

    if not stop_on_failure:
        totals.cases = list(await asyncio.gather(*(bounded(c) for c in cases)))
        return totals

    ordered = sorted(cases, key=lambda c: (not c.is_sample, c.position))
    by_id: dict[int, CaseResult] = {}
    batch_size = max(1, settings.max_concurrency)
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        for result in await asyncio.gather(*(bounded(c) for c in batch)):
            by_id[result.test_id] = result
        if any(not by_id[c.id].passed for c in batch):
            for case in ordered[start + batch_size :]:
                by_id[case.id] = _skipped(case)
            break
    totals.cases = [by_id[c.id] for c in cases]
    return totals
