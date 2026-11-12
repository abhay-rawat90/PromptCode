from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def make_engine(url: str, **pool: int) -> Engine:
    """An engine for `url`, tuned for SQLite (development) or Postgres (production)."""
    if url.startswith("sqlite"):
        sqlite_engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(sqlite_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            cur.close()

        return sqlite_engine

    connect_args: dict = {"connect_timeout": 15}  # a sleeping serverless database (Neon) wakes in ~1s
    if "-pooler." in url:
        # Neon's pooled hostname runs PgBouncer in transaction mode, which can't keep the
        # prepared statements psycopg creates for repeated queries on one connection.
        connect_args["prepare_threshold"] = None
    return create_engine(
        url,
        pool_size=pool.get("pool_size", 5),
        max_overflow=pool.get("max_overflow", 10),
        pool_pre_ping=True,  # replaces connections the server closed (e.g. while it slept)
        pool_recycle=300,
        connect_args=connect_args,
    )


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
