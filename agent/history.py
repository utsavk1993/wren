"""Keeping the record of a call so it can be looked at afterwards.

Everything about a call is already collected while it runs. This is what makes
it survive the call, which is the only way to answer the questions that come
later: why did the agent say that, why did it take four seconds, did it look
anything up before giving those steps.

The row is rewritten after every turn rather than written once at the end. A
call that drops halfway is exactly the call worth reading, and one that only
saves on a clean finish never saves that one.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from observability.logging import redact

log = logging.getLogger(__name__)


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://wren:wren@db:5432/wren")


def _search_path() -> str:
    """Where the call records live.

    Its own schema when the database is shared with the systems the agent reads
    from, and the ordinary one when it is not.
    """
    return os.getenv("WREN_SEARCH_PATH", "public")


def save(record: Any, *, customer_external_id: str | None, verified: bool,
         escalated: bool = False, ended: bool = False) -> None:
    """Write the call as it currently stands.

    Failing here must never interrupt a call. Losing the record of a
    conversation is a smaller problem than dropping the conversation.
    """
    try:
        with psycopg.connect(_database_url(), connect_timeout=3) as conn:
            conn.execute(f"SET search_path TO {_search_path()}")
            conn.execute(
                """
                INSERT INTO calls (
                    id, customer_external_id, verified, escalated,
                    transcript, tool_calls, refusals, timings,
                    updated_at, ended_at
                ) VALUES (
                    %(id)s, %(customer)s, %(verified)s, %(escalated)s,
                    %(transcript)s, %(tools)s, %(refusals)s, %(timings)s,
                    now(), %(ended_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    customer_external_id = EXCLUDED.customer_external_id,
                    verified   = EXCLUDED.verified,
                    escalated  = EXCLUDED.escalated,
                    transcript = EXCLUDED.transcript,
                    tool_calls = EXCLUDED.tool_calls,
                    refusals   = EXCLUDED.refusals,
                    timings    = EXCLUDED.timings,
                    updated_at = now(),
                    ended_at   = EXCLUDED.ended_at
                """,
                {
                    "id": record.id,
                    "customer": customer_external_id,
                    "verified": verified,
                    "escalated": escalated,
                    # Redacted on the way in, not on the way out. Anything
                    # stored unredacted is one query away from being read.
                    "transcript": json.dumps(redact(
                        [{"speaker": t.speaker, "text": t.text, "at": t.at}
                         for t in record.turns]
                    )),
                    "tools": json.dumps(redact(record.tool_calls), default=str),
                    "refusals": json.dumps(record.denials),
                    "timings": json.dumps(record.timings),
                    "ended_at": datetime.now(timezone.utc) if ended else None,
                },
            )
            conn.commit()
    except Exception:
        log.warning("could not record call %s", record.id, exc_info=True)


def list_calls(limit: int = 50) -> list[dict]:
    """Recent calls, newest first, with enough to choose one to open."""
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        conn.execute(f"SET search_path TO {_search_path()}")
        return conn.execute(
            """
            SELECT id, customer_external_id, verified, escalated,
                   started_at, ended_at,
                   jsonb_array_length(transcript) AS turns,
                   jsonb_array_length(tool_calls) AS tool_calls,
                   jsonb_array_length(refusals)   AS refusals,
                   (SELECT coalesce(sum((t->>'ms_total')::numeric), 0)
                      FROM jsonb_array_elements(timings) t) AS total_ms
            FROM calls
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()


def get_call(call_id: str) -> dict | None:
    """One call in full."""
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        conn.execute(f"SET search_path TO {_search_path()}")
        return conn.execute("SELECT * FROM calls WHERE id = %s", (call_id,)).fetchone()
