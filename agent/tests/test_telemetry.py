"""Checks against the real telemetry platform."""

from __future__ import annotations

import os

import httpx
import pytest

from systems.models import DeviceStatus
from systems.telemetry import TelemetryClient, TelemetryError

pytestmark = pytest.mark.skipif(
    not os.environ.get("SUPABASE_SECRET_KEY"),
    reason="needs credentials for the telemetry platform",
)

KNOWN_CUSTOMER = "CUST-1001"
REPEAT_FAILURE_CUSTOMER = "CUST-1050"


@pytest.fixture
async def client():
    telemetry = TelemetryClient()
    yield telemetry
    await telemetry.aclose()


async def test_household_equipment_comes_back(client):
    devices = await client.get_devices(KNOWN_CUSTOMER)
    assert devices
    assert all(d.customer_external_id == KNOWN_CUSTOMER for d in devices)
    assert all(isinstance(d.status, DeviceStatus) for d in devices)


async def test_faulty_equipment_is_listed_first(client):
    devices = await client.get_devices(REPEAT_FAILURE_CUSTOMER)
    faulty = [i for i, d in enumerate(devices) if d.is_faulty]
    healthy = [i for i, d in enumerate(devices) if not d.is_faulty]
    assert faulty, "expected this household to have something broken"
    assert max(faulty) < min(healthy), "faulty equipment should sort ahead of working equipment"


async def test_unknown_household_returns_nothing(client):
    assert await client.get_devices("CUST-9999") == []


async def test_unknown_device_is_not_an_error(client):
    assert await client.get_device("DEV-0000") is None


async def test_repeat_failure_equipment_is_flagged_unrecoverable(client):
    devices = await client.get_devices(REPEAT_FAILURE_CUSTOMER)
    stuck = [d for d in devices if d.is_faulty and not d.recovers_on_reset]
    assert stuck, "the repeat-failure household should own equipment a reset will not fix"


async def test_status_change_persists(client):
    devices = await client.get_devices(KNOWN_CUSTOMER)
    target = next(d for d in devices if not d.is_faulty)
    try:
        updated = await client.set_device_status(target.external_id, DeviceStatus.OFFLINE)
        assert updated.status is DeviceStatus.OFFLINE
        assert (await client.get_device(target.external_id)).status is DeviceStatus.OFFLINE
    finally:
        await client.set_device_status(target.external_id, target.status)
    assert (await client.get_device(target.external_id)).status is target.status


async def test_unreachable_platform_surfaces_as_a_typed_failure():
    async with httpx.AsyncClient(timeout=0.001) as http:
        client = TelemetryClient(http=http)
        with pytest.raises(TelemetryError):
            await client.get_devices(KNOWN_CUSTOMER)
