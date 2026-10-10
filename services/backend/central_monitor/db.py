from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .settings import get_settings

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def connect() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(get_settings().central_database_url, row_factory=dict_row)


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

