"""Per-model pacing for free-tier rate limits.

Each model has its own requests-per-minute (and, for Groq, tokens-per-minute) allowance. Instead
of firing every request and collecting 429s, callers ask `delay()` how long until a request fits
in the last-minute window, wait that long (or use a backup model), then `reserve()` a slot.

When a provider says a quota is used up for a long time (e.g. the free tier's requests-per-day),
the model is `block()`ed so later requests skip it instantly instead of wasting a call.

State lives in this process. That matches the deployment (one worker); with several workers each
paces separately, and the 429 handling in llm.py still catches anything that slips through.
"""

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger("promptcode.quota")

# (requests/minute, tokens/minute, requests/day); 0 = unknown / no limit. Groq numbers are
# corrected automatically from its rate-limit response headers. Gemini doesn't publish free-tier
# numbers: check https://aistudio.google.com/rate-limit and override with MODEL_LIMITS.
DEFAULT_LIMITS: dict[str, tuple[int, int, int]] = {
    "gemini-3.5-flash-lite": (10, 0, 500),
    "gemini-3.6-flash": (10, 0, 250),
    "gemini-3.8-flash": (5, 0, 20),
    "openai/gpt-oss-20b": (30, 8000, 1000),
    "openai/gpt-oss-120b": (30, 8000, 1000),
    "qwen/qwen3.8-27b": (30, 6000, 1000),
}
UNKNOWN_LIMITS = (10, 0, 0)
WINDOW = 60.0


def _configured_limits() -> dict[str, tuple[int, int, int]]:
    limits = dict(DEFAULT_LIMITS)
    for item in filter(None, (p.strip() for p in settings.model_limits.split(","))):
        name, _, numbers = item.partition("=")
        try:
            rpm, tpm, rpd = (int(n or 0) for n in (numbers.split(":") + ["0", "0"])[:3])
        except ValueError:
            log.warning("Ignoring malformed MODEL_LIMITS entry %r (expected model=rpm:tpm:rpd)", item)
            continue
        name = name.strip()
        if name.startswith(("gemini:", "groq:")):
            name = name.split(":", 1)[1]
        limits[name] = (rpm, tpm, rpd)
    return limits


@dataclass
class ModelState:
    label: str  # "provider:model"
    rpm: int
    tpm: int
    rpd: int
    window: deque = field(default_factory=deque)  # [monotonic time, tokens] per request
    cool_until: float = 0.0  # monotonic: a short per-minute 429 asked us to back off
    blocked_until: float = 0.0  # wall clock: quota used up for a long time
    block_reason: str = ""
    remaining_today: int | None = None  # provider-reported requests left (Groq headers)
    waiting: int = 0  # requests currently queued for this model

    def _prune(self, now: float) -> None:
        while self.window and now - self.window[0][0] >= WINDOW:
            self.window.popleft()

    def delay(self, est_tokens: int) -> float:
        """Seconds until a request of about `est_tokens` fits this model's per-minute limits."""
        now = time.monotonic()
        self._prune(now)
        wait = max(0.0, self.cool_until - now)
        if self.rpm and len(self.window) >= self.rpm:
            oldest_that_must_expire = self.window[len(self.window) - self.rpm][0]
            wait = max(wait, WINDOW - (now - oldest_that_must_expire))
        if self.tpm and self.window:
            used = sum(tokens for _, tokens in self.window)
            excess = used + est_tokens - self.tpm
            if excess > 0:
                freed = 0
                for started, tokens in self.window:
                    freed += tokens
                    if freed >= excess:
                        break
                wait = max(wait, WINDOW - (now - started))
        return wait

    def reserve(self, est_tokens: int) -> list:
        entry = [time.monotonic(), est_tokens]
        self.window.append(entry)
        return entry

    @staticmethod
    def settle(entry: list, actual_tokens: int) -> None:
        entry[1] = actual_tokens

    def cooldown(self, seconds: float) -> None:
        self.cool_until = max(self.cool_until, time.monotonic() + seconds)

    def block(self, seconds: float, reason: str) -> None:
        self.blocked_until = max(self.blocked_until, time.time() + seconds)
        self.block_reason = reason
        log.warning("%s unavailable for %.0f min: %s", self.label, seconds / 60, reason)

    def blocked(self) -> bool:
        return time.time() < self.blocked_until

    def used_last_minute(self) -> int:
        self._prune(time.monotonic())
        return len(self.window)

    def update_from_groq_headers(self, headers) -> None:
        """Groq reports the real limits: x-ratelimit-limit-requests is per day, -tokens per minute."""
        try:
            if value := headers.get("x-ratelimit-limit-requests"):
                self.rpd = int(value)
            if value := headers.get("x-ratelimit-remaining-requests"):
                self.remaining_today = int(value)
            if value := headers.get("x-ratelimit-limit-tokens"):
                self.tpm = int(value)
        except ValueError:
            pass


_states: dict[str, ModelState] = {}


def state(label: str) -> ModelState:
    if label not in _states:
        name = label.split(":", 1)[-1]
        rpm, tpm, rpd = _configured_limits().get(name, UNKNOWN_LIMITS)
        _states[label] = ModelState(label, rpm, tpm, rpd)
    return _states[label]


def reset() -> None:
    """Forget all pacing state (tests)."""
    _states.clear()


def parse_duration(text: str) -> float | None:
    """'1m26.4s' / '14h52m5s' / '945ms' / '30.6' -> seconds."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    if match := re.fullmatch(r"([\d.]+)ms", text):
        return float(match.group(1)) / 1000
    if match := re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", text):
        h, m, s = (float(g) if g else 0.0 for g in match.groups())
        if h or m or s:
            return h * 3600 + m * 60 + s
    return None
