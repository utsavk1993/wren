"""Database connection handling."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wren:wren@db:5432/wren")


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Open a connection that commits on success and rolls back on error.

    Rows come back as dictionaries so callers can address columns by name
    rather than by position.
    """
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wait_for_database(attempts: int = 30, delay_seconds: float = 1.0) -> None:
    """Block until Postgres accepts connections.

    Compose waits for the container's health check, but the server can still
    refuse a connection for a moment after that while it finishes recovery.
    """
    import time

    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(delay_seconds)
    raise RuntimeError(f"database unreachable after {attempts} attempts") from last_error
