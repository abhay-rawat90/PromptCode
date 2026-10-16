"""Transactional email: verification and password-reset links.

Backends (EMAIL_BACKEND):
- "resend":  sends through https://resend.com (needs RESEND_API_KEY and a verified sender).
- "console": logs the email instead of sending it, for local development. The link to click
             appears in the backend terminal.
Every sent message is also appended to `outbox` (the last few), which tests read.
"""

import html
import logging
from collections import deque
from dataclasses import dataclass

import requests

from .config import settings

log = logging.getLogger("promptcode.email")
RESEND_URL = "https://api.resend.com/emails"


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str


outbox: deque[Email] = deque(maxlen=50)


def send(message: Email) -> bool:
    """Send an email. Never raises: a failed email must not fail the request that caused it."""
    outbox.append(message)
    if settings.email_backend == "console":
        log.info("EMAIL (console backend - not sent)\nTo: %s\nSubject: %s\n\n%s", message.to, message.subject, message.text)
        return True
    try:
        resp = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": [message.to],
                "subject": message.subject,
                "text": message.text,
                "html": message.html,
            },
            timeout=10,
        )
    except requests.RequestException:
        log.exception("Could not reach Resend to email %s", message.to)
        return False
    if resp.status_code >= 300:
        log.error("Resend rejected email to %s: %s %s", message.to, resp.status_code, resp.text[:300])
        return False
    return True


def _render(title: str, intro: str, button: str, link: str, outro: str) -> tuple[str, str]:
    text = f"{title}\n\n{intro}\n\n{button}: {link}\n\n{outro}\n\n- PromptCode"
    safe_link = html.escape(link, quote=True)
    body = f"""<!doctype html><html><body style="margin:0;background:#f6f7f9;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#1a1d23">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#fff;border:1px solid #e2e5ea;border-radius:12px">
<tr><td style="padding:28px">
<div style="font:700 18px ui-monospace,Menlo,monospace;color:#e8900c;margin-bottom:18px">{{ }} PromptCode</div>
<h1 style="font-size:20px;margin:0 0 12px">{html.escape(title)}</h1>
<p style="font-size:15px;line-height:1.55;margin:0 0 22px;color:#3d434d">{html.escape(intro)}</p>
<a href="{safe_link}" style="display:inline-block;background:#e8900c;color:#fff;text-decoration:none;font-weight:600;padding:11px 20px;border-radius:8px">{html.escape(button)}</a>
<p style="font-size:13px;line-height:1.5;margin:22px 0 0;color:#646d7b">{html.escape(outro)}</p>
<p style="font-size:12px;line-height:1.5;margin:16px 0 0;color:#8e96a3;word-break:break-all">Button not working? Paste this link into your browser:<br>{safe_link}</p>
</td></tr></table></td></tr></table></body></html>"""
    return text, body


def send_verification(to: str, username: str, token: str) -> bool:
    link = f"{settings.app_url}/verify-email?token={token}"
    text, body = _render(
        "Confirm your email",
        f"Hi {username}, thanks for joining PromptCode! Confirm this is your email address so you can publish problems and recover your account.",
        "Confirm email",
        link,
        "This link expires in 48 hours. If you didn't create an account, you can ignore this email.",
    )
    return send(Email(to, "Confirm your PromptCode email", text, body))


def send_password_reset(to: str, username: str, token: str) -> bool:
    link = f"{settings.app_url}/reset-password?token={token}"
    text, body = _render(
        "Reset your password",
        f"Hi {username}, someone (hopefully you) asked to reset the password for your PromptCode account.",
        "Choose a new password",
        link,
        "This link expires in 1 hour and can be used once. If you didn't ask for this, ignore this email - your password stays the same.",
    )
    return send(Email(to, "Reset your PromptCode password", text, body))
