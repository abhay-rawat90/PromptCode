"""Username and password rules. Mirrored in frontend/src/validation.ts - keep them in sync."""

import re
from pathlib import Path

USERNAME_MIN, USERNAME_MAX = 3, 20
PASSWORD_MIN, PASSWORD_MAX = 8, 128

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "sysadmin", "support", "help", "api", "www",
    "mail", "email", "promptcode", "moderator", "mod", "staff", "official", "security",
    "login", "logout", "register", "signup", "signin", "settings", "profile", "account",
    "problems", "problem", "submissions", "leaderboard", "user", "users", "new", "edit",
    "null", "undefined", "anonymous", "guest", "test", "owner", "billing",
}

_COMMON_PASSWORDS = {
    line.strip().lower()
    for line in (Path(__file__).parent / "common_passwords.txt").read_text().splitlines()
    if line.strip()
}


def username_problems(username: str) -> list[str]:
    errors = []
    if not USERNAME_MIN <= len(username) <= USERNAME_MAX:
        errors.append(f"must be {USERNAME_MIN}-{USERNAME_MAX} characters")
    if not _USERNAME_RE.match(username):
        errors.append("must start with a letter and use only letters, numbers and underscores")
    if "__" in username:
        errors.append("cannot contain consecutive underscores")
    if username.endswith("_"):
        errors.append("cannot end with an underscore")
    if username.lower() in RESERVED_USERNAMES:
        errors.append("is reserved")
    return errors


def password_problems(password: str, username: str = "", email: str = "") -> list[str]:
    errors = []
    if len(password) < PASSWORD_MIN:
        errors.append(f"at least {PASSWORD_MIN} characters")
    if len(password) > PASSWORD_MAX:
        errors.append(f"at most {PASSWORD_MAX} characters")
    if not re.search(r"[a-z]", password):
        errors.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        errors.append("an uppercase letter")
    if not re.search(r"\d", password):
        errors.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("a symbol")
    if password != password.strip():
        errors.append("no spaces at the start or end")

    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        errors.append("to not be a commonly used password")
    personal = [username.lower(), email.split("@")[0].lower()]
    if any(p and len(p) >= 3 and p in lowered for p in personal):
        errors.append("to not contain your username or email")
    return errors


def password_error_message(problems: list[str]) -> str:
    return "Password needs: " + ", ".join(problems) + "."
