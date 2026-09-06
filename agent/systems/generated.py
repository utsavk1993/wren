"""Types describing the telemetry tables.

Generated from the schema the platform publishes about itself. Do not edit;
run `python seed/gen_types.py` after changing the tables.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: F401
from typing import Literal

from pydantic import BaseModel

DeviceKind = Literal["control_panel", "door_sensor", "window_sensor", "motion_sensor", "camera", "keypad"]
DeviceStatus = Literal["online", "offline", "low_battery"]


class Device(BaseModel):
    """One row of the devices table."""

    external_id: str
    customer_external_id: str
    name: str
    device_type: DeviceKind
    status: DeviceStatus
    battery_pct: int | None = None
    last_seen: datetime | None = None
    recovers_on_reset: bool
    notes: str
    updated_at: datetime
