"""Streak reminder emails: once a day, a few hours before the daily problem resets, email users
whose streak is about to end.

The background scheduler calls `send_due()` every few minutes. On hosts where the web service
sleeps (Render's free plan), run `python -m app.reminders` from a cron job at REMINDER_HOUR_UTC.
"""

import hashlib
import hmac
import logging
from datetime import timedelta

from sqlalchemy import func, select

from . import daily, emails
from .config import settings
from .db import SessionLocal
from .models import DailyChallenge, User

log = logging.getLogger("promptcode.reminders")
# The day whose reminders are finished: later calls that day return without touching the database.
_done_day = None


# ---------- unsubscribe links (no login needed, so they're signed) ----------

def _signature(user_id: int) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"unsubscribe:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_token(user_id: int) -> str:
    return f"{user_id}.{_signature(user_id)}"


def user_id_from_token(token: str) -> int | None:
    raw_id, _, signature = token.partition(".")
    if not raw_id.isdigit() or not hmac.compare_digest(signature, _signature(int(raw_id))):
        return None
    return int(raw_id)


# ---------- sending ----------

def send_due() -> int:
    """Send today's reminders if it's time. Safe to call often: each user gets at most one a day,
    and the daily cap is never exceeded. Returns how many were sent."""
    if not (settings.reminders_enabled and settings.daily_enabled):
        return 0
    global _done_day
    now, day = daily.now(), daily.today()
    if now.hour < settings.reminder_hour_utc or _done_day == day:
        return 0

    with SessionLocal() as db:
        challenge = db.scalar(select(DailyChallenge).where(DailyChallenge.day == day))
        if challenge is None or challenge.problem.hidden_at is not None:
            return 0  # nothing to solve today
        already = db.scalar(select(func.count()).select_from(User).where(User.last_reminded == day)) or 0
        room = settings.reminder_daily_cap - already
        if room <= 0:
            return 0
        # A live streak that today's problem would extend: solved yesterday, not yet today.
        users = db.scalars(
            select(User)
            .where(
                User.reminder_emails.is_(True),
                User.email_verified.is_(True),
                User.current_streak >= 1,
                User.last_daily_solved == day - timedelta(days=1),
                (User.last_reminded.is_(None)) | (User.last_reminded != day),
            )
            .order_by(User.current_streak.desc(), User.id)  # longest streaks first if the cap bites
            .limit(room)
        ).all()

        hours_left = max(1, round((daily.next_reset() - now).total_seconds() / 3600))
        sent = 0
        for user in users:
            user.last_reminded = day  # marked first: a crash must not cause a second email
            db.commit()
            if emails.send_streak_reminder(
                user.email, user.username, user.current_streak, hours_left,
                challenge.problem.slug, challenge.problem.title, unsubscribe_token(user.id),
            ):
                sent += 1
        if users:
            log.info("Streak reminders: sent %d of %d", sent, len(users))
        if len(users) < room:
            _done_day = day  # everyone due has been handled (if the cap bit, try again later)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"Sent {send_due()} streak reminder(s).")
