"""Schema application.

Postgres only runs the files in its init directory when the data directory is
empty, so a database created before a schema change never receives it. Applying
the same file on every startup closes that gap: every statement is guarded with
IF NOT EXISTS, so running it against an up-to-date database does nothing.
"""

import logging
import os
from pathlib import Path

from db import connection

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "db" / "init.sql"


def apply_schema() -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"schema file not found at {SCHEMA_PATH}")

    with connection() as conn:
        # The agent's own tables sit in their own schema when the database is
        # shared with the systems it reads from. Locally there is nothing to
        # share and this is a no-op.
        conn.execute(f"SET search_path TO {os.getenv('WREN_SEARCH_PATH', 'public')}")
        conn.execute(SCHEMA_PATH.read_text())

    log.info("schema applied from %s", SCHEMA_PATH)
