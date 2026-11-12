"""Copy every PromptCode table from one database to another.

Uses:
  - moving to a new host:   python -m app.dbcopy --from "<render url>" --to "<neon url>"
  - a backup on your PC:    python -m app.dbcopy --from "<neon url>" --to sqlite:///backup-2026-11-12.db

The target is first migrated to the latest schema. It must be empty, unless you pass --replace,
which deletes everything in the target first. The answer cache (llm_cache) is skipped unless
--include-cache is given: it's only a cache and can be large.

Both databases must be on the same schema version: if the source is older, start the app on
it once (it migrates on startup) or run `alembic upgrade head` against it.
"""

import argparse
import sys

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from . import models  # noqa: F401 - registers every table on Base.metadata
from .config import _database_url
from .db import Base, make_engine
from .migrations import upgrade_database

BATCH = 1000
CACHE_TABLE = "llm_cache"


def _schema_version(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:  # noqa: BLE001 - no alembic_version table: not a PromptCode database yet
            return None


def _tables(include_cache: bool):
    """Tables in foreign-key order (parents before children)."""
    return [t for t in Base.metadata.sorted_tables if include_cache or t.name != CACHE_TABLE]


def _row_count(engine: Engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar() or 0


def _reset_sequences(conn, tables) -> None:
    """After inserting rows with explicit ids, move each Postgres id sequence past them."""
    for table in tables:
        if "id" not in table.c:
            continue
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), "
            f"(SELECT MAX(id) FROM {table.name}) IS NOT NULL)"
        ))


def copy_database(source_url: str, target_url: str, *, replace: bool = False, include_cache: bool = False,
                  log=print) -> dict[str, int]:
    """Copy all rows from source to target. Returns {table: rows copied}."""
    source_url, target_url = _database_url(source_url), _database_url(target_url)
    if source_url == target_url:
        raise ValueError("The source and target are the same database.")
    source, target = make_engine(source_url, pool_size=1), make_engine(target_url, pool_size=1)
    try:
        source_version = _schema_version(source)
        if source_version is None:
            raise ValueError("The source doesn't look like a PromptCode database (no alembic_version table).")

        log("Migrating the target to the latest schema…")
        upgrade_database(target_url)
        target_version = _schema_version(target)
        if source_version != target_version:
            raise ValueError(
                f"Schema versions differ (source {source_version}, target {target_version}). "
                "Start the app against the source once so it migrates, then try again."
            )

        tables = _tables(include_cache)
        non_empty = [t.name for t in tables if _row_count(target, t)]
        if non_empty and not replace:
            raise ValueError(
                "The target already has data in: " + ", ".join(non_empty)
                + ". Use --replace to delete it first (only if you're sure)."
            )

        copied: dict[str, int] = {}
        with source.connect() as src, target.begin() as dst:
            if replace:
                for table in reversed(_tables(include_cache=True)):
                    dst.execute(table.delete())
            for table in tables:
                count = 0
                result = src.execution_options(yield_per=BATCH).execute(select(table))
                for rows in result.partitions():
                    dst.execute(table.insert(), [dict(row._mapping) for row in rows])
                    count += len(rows)
                copied[table.name] = count
                log(f"  {table.name}: {count} rows")
            if target.dialect.name == "postgresql":
                _reset_sequences(dst, tables)
        return copied
    finally:
        source.dispose()
        target.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy all PromptCode data from one database to another.")
    parser.add_argument("--from", dest="source", required=True, help="source database URL")
    parser.add_argument("--to", dest="target", required=True, help="target database URL (Postgres or sqlite:///file.db)")
    parser.add_argument("--replace", action="store_true", help="delete existing data in the target first")
    parser.add_argument("--include-cache", action="store_true", help="also copy stored AI answers")
    args = parser.parse_args(argv)
    try:
        copied = copy_database(args.source, args.target, replace=args.replace, include_cache=args.include_cache)
    except ValueError as exc:
        print(f"Not copied: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {sum(copied.values())} rows in {len(copied)} tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
