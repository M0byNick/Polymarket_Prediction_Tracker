"""SQLite connection helper.

Wraps the common pattern used across the project:

    - Ensure parent directory exists.
    - Open in WAL mode for concurrent read/write.
    - Register ``sqlite3.Row`` as the row factory so columns are accessible
      by name.
    - Apply additive column migrations (registered via
      :func:`register_migration`) idempotently on each connect.

The migration system is intentionally minimal: it only supports
``ALTER TABLE ... ADD COLUMN`` statements because that's all we've
needed. SQLite's ``ADD COLUMN`` is safe (O(1), no table rewrite on
modern versions) and can be replayed harmlessly via the
``PRAGMA table_info`` check.

Schemas live in dedicated modules (e.g. ``src.paper_schema``) and
register themselves by importing this module and calling
``register_ddl`` / ``register_migration``.

Usage::

    from src.db import connect
    with connect("data/polymarket_snapshots.db") as conn:
        conn.execute("...")
        conn.commit()
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Registered DDL statements. Each is executed via ``executescript`` on
# every connect — they must all be idempotent (``IF NOT EXISTS`` etc).
_DDL: list[str] = []

# Registered additive migrations: (table, column, column_type). SQLite
# does not accept ``IF NOT EXISTS`` on ``ALTER TABLE ADD COLUMN``, so we
# check ``PRAGMA table_info`` first.
_MIGRATIONS: list[tuple[str, str, str]] = []


def register_ddl(ddl: str) -> None:
    """Register an idempotent DDL block to run on each connect."""
    _DDL.append(ddl)


def register_migration(table: str, column: str, col_type: str) -> None:
    """Register an additive migration (``ALTER TABLE ADD COLUMN``).

    The migration is a no-op when the column already exists.
    """
    _MIGRATIONS.append((table, column, col_type))


def _apply_migrations(conn: sqlite3.Connection) -> None:
    table_cache: dict[str, set[str]] = {}
    for table, col, col_type in _MIGRATIONS:
        if table not in table_cache:
            # If table doesn't exist yet, PRAGMA returns empty — treat as
            # "all columns missing" and let the ALTER fail loudly if the
            # underlying table really is absent. In practice the table
            # should be created by registered DDL before migrations run.
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            table_cache[table] = {r[1] for r in rows}
        if not table_cache[table]:
            # Table missing — skip migration; the DDL should have created
            # it. Callers that register migrations for nonexistent tables
            # will get a SQL error on the ALTER below, which is
            # deliberate.
            pass
        if col not in table_cache[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            table_cache[table].add(col)


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a SQLite connection with schema applied.

    Creates parent directories if needed. Sets WAL + NORMAL sync, runs
    registered DDL (idempotent) and additive migrations, and installs
    ``sqlite3.Row`` as the row factory.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        for ddl in _DDL:
            conn.executescript(ddl)
        _apply_migrations(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()
