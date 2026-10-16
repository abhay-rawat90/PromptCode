"""Admin CLI.  Usage:  python -m app.admin make-admin <username>  |  python -m app.admin remove-admin <username>"""

import sys

from sqlalchemy import func, select

from .db import SessionLocal
from .models import User


def set_admin(username: str, value: bool) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if user is None:
            sys.exit(f"No user named {username!r}.")
        user.is_admin = value
        db.commit()
        print(f"{user.username} is {'now' if value else 'no longer'} an admin.")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("make-admin", "remove-admin"):
        sys.exit(__doc__)
    set_admin(sys.argv[2], sys.argv[1] == "make-admin")
