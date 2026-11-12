from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def upgrade_database(database_url: str | None = None) -> None:
    """Apply any pending Alembic migrations (same as `alembic upgrade head`) to the app's
    database, or to `database_url` if given."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["configure_logger"] = False  # keep uvicorn's logging config
    if database_url:
        cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")
