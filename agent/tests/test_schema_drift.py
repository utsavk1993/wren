"""Checks that the shapes declared here still match the systems they describe.

Neither connected system hands over usable types. The device platform publishes
a schema, but the constraint restricting a device's status to three values is
not in it, so anything generated from it would type that column as an ordinary
string. The customer system publishes no types at all, though what it does
publish is richer: every field arrives with its allowed values.

So the types stay hand written, and these tests watch for the systems moving
away from them. That matters more than it sounds. An unrecognised value is
deliberately treated as the unsafe case, which means a status nobody here has
heard of makes every account carrying it look suspended, and a device status
nobody has heard of makes working equipment look broken. Both are the right
call when it happens, and both are silent, so something has to say when the
systems have changed.
"""

from __future__ import annotations

import os

import httpx
import pytest

from systems.models import AccountStatus, DeviceStatus
from systems.salesforce import API_VERSION, SalesforceClient

needs_telemetry = pytest.mark.skipif(
    not os.environ.get("SUPABASE_SECRET_KEY"),
    reason="needs credentials for the telemetry platform",
)
needs_customers = pytest.mark.skipif(
    not os.environ.get("SALESFORCE_CLIENT_ID"),
    reason="needs credentials for the customer system",
)

# Columns this code reads. Extra ones appearing is fine; these disappearing is
# not, and would otherwise show up as every device silently losing a field.
EXPECTED_DEVICE_COLUMNS = {
    "external_id",
    "customer_external_id",
    "name",
    "device_type",
    "status",
    "battery_pct",
    "last_seen",
    "recovers_on_reset",
    "notes",
}

EXPECTED_ACCOUNT_FIELDS = {
    "Wren_External_Id__c",
    "Plan__c",
    "Account_Status__c",
    "Status_Since__c",
    "Verbal_Passcode__c",
}

EXPECTED_CASE_FIELDS = {
    "Wren_External_Id__c",
    "Wren_Device_External_Id__c",
    "Wren_Occurred_On__c",
}


@needs_telemetry
async def test_the_device_table_still_has_the_columns_this_code_reads():
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SECRET_KEY"]
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(
            f"{base}/rest/v1/",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
    response.raise_for_status()
    published = set(response.json()["definitions"]["devices"]["properties"])
    missing = EXPECTED_DEVICE_COLUMNS - published
    assert not missing, f"the device table no longer has: {sorted(missing)}"


@needs_customers
async def test_account_status_values_still_match_the_ones_declared_here():
    """The values are what make this worth checking.

    An account status added over there and not here is read as suspended, which
    stops the agent troubleshooting for those households entirely. That is the
    safe behaviour, and nothing about it is visible until someone notices the
    calls going nowhere.
    """
    client = SalesforceClient()
    try:
        token = await client._access_token()
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(
                f"{client.instance_url}/services/data/{API_VERSION}/sobjects/Account/describe",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        fields = {f["name"]: f for f in response.json()["fields"]}
    finally:
        await client.aclose()

    missing = EXPECTED_ACCOUNT_FIELDS - set(fields)
    assert not missing, f"the customer system no longer has: {sorted(missing)}"

    over_there = {v["value"] for v in fields["Account_Status__c"]["picklistValues"]}
    over_here = {s.value for s in AccountStatus}
    assert over_there == over_here, (
        f"account statuses have diverged. "
        f"only in the customer system: {sorted(over_there - over_here)}; "
        f"only here: {sorted(over_here - over_there)}"
    )


@needs_customers
async def test_the_external_identifier_is_still_an_external_identifier():
    """Losing this flag turns every reload into duplicate records."""
    client = SalesforceClient()
    try:
        token = await client._access_token()
        async with httpx.AsyncClient(timeout=10) as http:
            for sobject in ("Account", "Contact", "Case"):
                response = await http.get(
                    f"{client.instance_url}/services/data/{API_VERSION}"
                    f"/sobjects/{sobject}/describe",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                field = next(
                    f for f in response.json()["fields"]
                    if f["name"] == "Wren_External_Id__c"
                )
                assert field["externalId"], f"{sobject} external id is no longer one"
                assert field["unique"], f"{sobject} external id is no longer unique"
    finally:
        await client.aclose()


@needs_customers
async def test_the_case_fields_this_code_writes_still_exist():
    client = SalesforceClient()
    try:
        token = await client._access_token()
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(
                f"{client.instance_url}/services/data/{API_VERSION}/sobjects/Case/describe",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        names = {f["name"] for f in response.json()["fields"]}
    finally:
        await client.aclose()
    missing = EXPECTED_CASE_FIELDS - names
    assert not missing, f"the customer system no longer has: {sorted(missing)}"


@needs_telemetry
async def test_the_generated_device_types_are_not_stale():
    """The device types are generated, so they cannot drift, only go stale.

    Nothing here restates what a device status may be; the values come from the
    database. What can go wrong is the table changing and nobody regenerating,
    so the check is that generating again would produce what is committed.
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "seed"))
    from gen_types import _fetch_schema, generate

    schema = _fetch_schema(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    fresh = generate(schema, ["devices"])
    committed = (root / "agent" / "systems" / "generated.py").read_text()
    assert fresh == committed, (
        "the telemetry tables have changed since the types were generated; "
        "run `python seed/gen_types.py`"
    )


def test_nothing_restates_what_a_device_status_may_be():
    """The values must have exactly one source, which is the database.

    A second list written out by hand is the thing this whole change removes,
    and it would go out of date silently.
    """
    from typing import get_args

    assert get_args(DeviceStatus), "the generated type carries the values"
    models = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "systems" / "models.py"
    ).read_text()
    for value in get_args(DeviceStatus):
        if value == "online":
            continue  # named once, as the value everything compares against
        assert f'"{value}"' not in models, (
            f"{value!r} is written out in models.py as well as being generated"
        )
