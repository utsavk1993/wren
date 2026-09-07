"""Opening a connection that can find both the tables and the vector type.

The agent's own tables live in their own schema, apart from what stands in for
the client's systems. Naming that schema in the connection string does not
survive a pooled connection: the pooler drops startup options, so the setting
never reaches the server and every query looks in the wrong place.

It is set on the connection instead, once it is open, which works either way.
"""

from __future__ import annotations

import os

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

# Where to look, in order. The agent's own schema first, then the ordinary one,
# then where a hosted database keeps its extensions.
DEFAULT_SEARCH_PATH = "wren, public, extensions"


def database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://wren:wren@db:5432/wren")


def connect(*, rows_as_dicts: bool = False) -> psycopg.Connection:
    """A connection that has already been told where to look.

    The vector type has to be registered after the search path is set, or the
    lookup runs against the wrong schemas and reports the type as missing even
    though it is installed.
    """
    conn = psycopg.connect(
        database_url(),
        connect_timeout=20,
        row_factory=dict_row if rows_as_dicts else None,
    )
    conn.execute(
        f"SET search_path TO {os.getenv('WREN_SEARCH_PATH', DEFAULT_SEARCH_PATH)}"
    )
    register_vector(conn)
    return conn
