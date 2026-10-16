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


def extract_json(output: str) -> str:
    """Strip a surrounding ```json fence if the model added one."""
    text = output.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1 and text.endswith("```"):
            text = text[first_newline + 1 : -3].strip()
    return text


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

    def public(self, case: TestCase) -> dict:
        """Serialized result. Hidden tests reveal only pass/fail (and infra errors)."""
        data = {
            "test_id": self.test_id,
            "position": self.position,
            "is_sample": self.is_sample,
            "check_type": self.check_type,
            "passed": self.passed,
            "error": self.error,
            "input": None,
            "expected": None,
            "output": None,
            "detail": self.detail if (self.is_sample or self.error) else None,
        }
        if self.is_sample:
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
    def status(self) -> str:
        if self.passed == len(self.cases):
            return "accepted"
        if any(c.error for c in self.cases):
            return "error"
        return "wrong_answer"


async def _grade_case(prompt: str, case: TestCase, totals: EvalResult) -> CaseResult:
    def result(passed: bool, output: str, detail: str | None, error: bool = False) -> CaseResult:
        return CaseResult(case.id, case.position, case.is_sample, case.check_type, passed, output, detail, error)

    run = await llm.run_prompt(prompt, case.input)
    totals.input_tokens += run.input_tokens
    totals.output_tokens += run.output_tokens
    if run.error:
        # A truncated output is the prompt's fault, not an infrastructure error.
        is_infra = not run.error.startswith("Output was cut off")
        return result(False, run.text, run.error, error=is_infra)

    if case.check_type in DETERMINISTIC_CHECKS:
        passed, detail = check_deterministic(case.check_type, case.expected, run.text, case.case_sensitive)
        return result(passed, run.text, detail)

    passed, reasoning, usage = await llm.judge(case.expected, case.input, run.text)
    totals.input_tokens += usage.input_tokens
    totals.output_tokens += usage.output_tokens
    if usage.error:
        return result(False, run.text, usage.error, error=True)
    return result(passed, run.text, reasoning or None)


async def evaluate(prompt: str, cases: list[TestCase]) -> EvalResult:
    totals = EvalResult()
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def bounded(case: TestCase) -> CaseResult:
        async with semaphore, _global_slots:
            return await _grade_case(prompt, case, totals)

    totals.cases = list(await asyncio.gather(*(bounded(c) for c in cases)))
    return totals
