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

from .models import Device, DeviceStatus, is_faulty

log = logging.getLogger(__name__)

# This call sits between the caller finishing a sentence and the agent starting
# to speak, so it is given a short deadline and allowed to fail rather than
# holding the turn open.
REQUEST_TIMEOUT_SECONDS = 4.0

# Asked for by name rather than with a wildcard, and taken from the generated
# record so the two cannot drift. Selecting less than the record needs fails at
# the point of reading, which is a confusing place to find out about it.
FIELDS = ",".join(Device.model_fields)


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
        return sorted(devices, key=lambda d: (not is_faulty(d), d.name))

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
                json={"status": status, "last_seen": datetime.now().astimezone().isoformat()},
            )
        except httpx.HTTPError as exc:
            raise TelemetryError(f"could not update device state: {exc}") from exc
        if response.status_code >= 400:
            raise TelemetryError(
                f"device update failed: HTTP {response.status_code} {response.text[:200]}"
            )
        rows = response.json()
        return _to_device(rows[0]) if rows else None


def _to_device(row: dict) -> Device:
    """Turn one row into the generated record.

    There is no coercion here on purpose. The columns have real types, so the
    database cannot hold a status outside the allowed set and the generated
    record will not accept one either. A row that fails to parse means the
    schema has moved, and hearing about that is better than quietly reading a
    broken sensor as healthy, which is what a lenient fallback would do.
    """
    return Device.model_validate(row)
