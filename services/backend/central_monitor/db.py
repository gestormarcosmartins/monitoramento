from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .settings import get_settings

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


@lru_cache
def get_pool() -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
    settings = get_settings()
    if settings.database_pool_min_size > settings.database_pool_max_size:
        raise ValueError("DATABASE_POOL_MIN_SIZE must not exceed DATABASE_POOL_MAX_SIZE")
    return ConnectionPool(
        conninfo=settings.central_database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        timeout=settings.database_pool_timeout_seconds,
        kwargs={"row_factory": dict_row},
        name="central-monitor-api",
        open=True,
    )


def connect() -> Any:
    """Return a pooled connection context manager.

    Callers must keep using ``with connect() as connection`` so transactions are
    committed or rolled back before the connection returns to the pool.
    """
    return get_pool().connection()


def pool_stats() -> dict[str, int | str]:
    stats = get_pool().get_stats()
    public_keys = (
        "pool_size",
        "pool_available",
        "requests_waiting",
        "requests_num",
        "requests_queued",
        "requests_errors",
        "requests_wait_ms",
    )
    return {key: stats[key] for key in public_keys if key in stats}


def migrate() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cm_schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM cm_schema_migrations")
        }
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if migration.name in applied:
                continue
            connection.execute(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO cm_schema_migrations(version) VALUES (%s)",
                (migration.name,),
            )


@contextmanager
def tenant_connection(tenant_id: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with connect() as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield connection


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as connection:
        return list(connection.execute(query, params).fetchall())

