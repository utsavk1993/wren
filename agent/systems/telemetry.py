"""Client for the device telemetry platform.

Holds the live state of installed equipment: whether it is reporting, how much
battery it has left, when it was last heard from, and whether a power cycle is
expected to bring it back.

Reads go over the platform's HTTP interface rather than a database connection,
which is what a service integrating with someone else's telemetry system would
actually be given.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx

from .models import Device, DeviceStatus

log = logging.getLogger(__name__)

# This call sits between the caller finishing a sentence and the agent starting
# to speak, so it is given a short deadline and allowed to fail rather than
# holding the turn open.
REQUEST_TIMEOUT_SECONDS = 4.0

FIELDS = (
    "external_id,customer_external_id,name,device_type,"
    "status,battery_pct,last_seen,recovers_on_reset,notes"
)


class TelemetryError(RuntimeError):
    """The telemetry platform could not be reached or refused the request."""


class TelemetryClient:
    def __init__(
        self,
        base_url: str | None = None,
        secret_key: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ["SUPABASE_URL"]).rstrip("/")
        key = secret_key or os.environ["SUPABASE_SECRET_KEY"]
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        self._http = http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _get(self, params: dict[str, str]) -> list[dict]:
        try:
            response = await self._http.get(
                f"{self.base_url}/rest/v1/devices", params=params, headers=self._headers
            )
        except httpx.TimeoutException as exc:
            raise TelemetryError("telemetry platform did not respond in time") from exc
        except httpx.HTTPError as exc:
            raise TelemetryError(f"telemetry platform unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise TelemetryError(
                f"telemetry query failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()

    async def get_devices(self, customer_external_id: str) -> list[Device]:
        """Everything installed at one household.

        Faulty equipment is ordered first: it is what the call is about, and a
        household can easily have fifteen devices of which one matters.
        """
        rows = await self._get({
            "customer_external_id": f"eq.{customer_external_id}",
            "select": FIELDS,
            "order": "name.asc",
        })
        devices = [_to_device(r) for r in rows]
        return sorted(devices, key=lambda d: (not d.is_faulty, d.name))

    async def get_device(self, device_external_id: str) -> Device | None:
        rows = await self._get({
            "external_id": f"eq.{device_external_id}",
            "select": FIELDS,
            "limit": "1",
        })
        return _to_device(rows[0]) if rows else None

    async def set_device_status(
        self, device_external_id: str, status: DeviceStatus
    ) -> Device | None:
        """Record a change in state, after a reset brings equipment back."""
        try:
            response = await self._http.patch(
                f"{self.base_url}/rest/v1/devices",
                params={"external_id": f"eq.{device_external_id}"},
                headers={**self._headers, "Prefer": "return=representation"},
                json={"status": status.value, "last_seen": datetime.now().astimezone().isoformat()},
            )
        except httpx.HTTPError as exc:
            raise TelemetryError(f"could not update device state: {exc}") from exc
        if response.status_code >= 400:
            raise TelemetryError(
                f"device update failed: HTTP {response.status_code} {response.text[:200]}"
            )
        rows = response.json()
        return _to_device(rows[0]) if rows else None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        log.warning("could not read last_seen value %r", value)
        return None


def _to_device(row: dict) -> Device:
    raw = row.get("status") or DeviceStatus.OFFLINE.value
    try:
        status = DeviceStatus(raw)
    except ValueError:
        # Equipment reporting something this code does not recognise is treated
        # as not reporting, rather than assumed healthy.
        log.warning("unrecognised device status %r, treating as offline", raw)
        status = DeviceStatus.OFFLINE
    return Device(
        external_id=row.get("external_id") or "",
        customer_external_id=row.get("customer_external_id") or "",
        name=row.get("name") or "",
        device_type=row.get("device_type") or "",
        status=status,
        battery_pct=row.get("battery_pct"),
        last_seen=_parse_timestamp(row.get("last_seen")),
        recovers_on_reset=bool(row.get("recovers_on_reset", True)),
        notes=row.get("notes") or "",
    )
