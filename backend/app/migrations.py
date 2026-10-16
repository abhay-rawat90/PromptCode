from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def upgrade_database() -> None:
    """Apply any pending Alembic migrations (same as `alembic upgrade head`)."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["configure_logger"] = False  # keep uvicorn's logging config
    command.upgrade(cfg, "head")
