"""Load the equipment fleet into the telemetry store.

Rows are keyed on the identifier this project assigns, so reloading updates
equipment in place rather than accumulating duplicates. Writes go through a
direct Postgres connection because bulk loading and schema work are what that
connection is for; the agent reads this data over the HTTP API instead, which
is what a service integrating with someone else's telemetry platform would
actually have.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg

from generate import Device, generate

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "supabase" / "schema.sql"
BATCH_SIZE = 500

UPSERT = """
INSERT INTO devices (
    external_id, customer_external_id, name, device_type,
    status, battery_pct, last_seen, recovers_on_reset, notes
) VALUES (
    %(external_id)s, %(customer_external_id)s, %(name)s, %(device_type)s,
    %(status)s, %(battery_pct)s, %(last_seen)s, %(recovers_on_reset)s, %(notes)s
)
ON CONFLICT (external_id) DO UPDATE SET
    customer_external_id = EXCLUDED.customer_external_id,
    name                 = EXCLUDED.name,
    device_type          = EXCLUDED.device_type,
    status               = EXCLUDED.status,
    battery_pct          = EXCLUDED.battery_pct,
    last_seen            = EXCLUDED.last_seen,
    recovers_on_reset    = EXCLUDED.recovers_on_reset,
    notes                = EXCLUDED.notes
"""


def _connection() -> psycopg.Connection:
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("SUPABASE_DB_URL is not set")
    return psycopg.connect(url, connect_timeout=20)


def apply_schema() -> None:
    """Create the table if it is not already there.

    Every statement is written to be safe to run against a database that
    already has them, so this can run before every load.
    """
    with _connection() as conn:
        conn.execute(SCHEMA_PATH.read_text())
        conn.commit()
    log.info("schema applied")


def _rows(devices: list[Device]) -> list[dict]:
    return [
        {
            "external_id": d.external_id,
            "customer_external_id": d.customer_external_id,
            "name": d.name,
            "device_type": d.device_type,
            "status": d.status,
            "battery_pct": d.battery_pct,
            "last_seen": d.last_seen,
            "recovers_on_reset": d.recovers_on_reset,
            "notes": d.notes,
        }
        for d in devices
    ]


def load() -> dict[str, int]:
    _, devices, _ = generate()
    rows = _rows(devices)

    with _connection() as conn:
        with conn.cursor() as cur:
            for start in range(0, len(rows), BATCH_SIZE):
                cur.executemany(UPSERT, rows[start:start + BATCH_SIZE])
        conn.commit()
        total = conn.execute("SELECT count(*) FROM devices").fetchone()[0]

    log.info("loaded %d devices", len(rows))
    return {"devices_sent": len(rows), "devices_in_table": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_schema()
    print(load())
