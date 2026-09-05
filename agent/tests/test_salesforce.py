"""Checks against the real customer system.

These talk to a live tenant rather than a stand-in, because the failures worth
catching here are the ones a stand-in would paper over: a field that is not
visible to the integration user, a status value nobody anticipated, a query
that is valid until the schema moves. They skip when no credentials are set.
"""

from __future__ import annotations

import os

import pytest

from typing import get_args

from systems.models import AccountStatus
from systems.salesforce import SalesforceClient, normalise_phone

pytestmark = pytest.mark.skipif(
    not os.environ.get("SALESFORCE_CLIENT_ID"),
    reason="needs credentials for the customer system",
)

KNOWN_PHONE = "+1-512-555-0135"
KNOWN_EXTERNAL_ID = "CUST-1001"
REPEAT_FAILURE_EXTERNAL_ID = "CUST-1050"


@pytest.fixture
async def client():
    sf = SalesforceClient()
    yield sf
    await sf.aclose()


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("+1-512-555-0135", "5125550135"),
        ("512 555 0135", "5125550135"),
        ("(512) 555-0135", "5125550135"),
        ("1 512 555 0135", "5125550135"),
        ("five one two...", ""),
        ("", ""),
    ],
)
def test_phone_normalisation(spoken, expected):
    assert normalise_phone(spoken) == expected


async def test_finds_a_household_however_the_number_is_spoken(client):
    for spoken in (KNOWN_PHONE, "512 555 0135", "(512) 555-0135", "1-512-555-0135"):
        customer = await client.find_customer_by_phone(spoken)
        assert customer is not None, f"no match for {spoken!r}"
        assert customer.external_id == KNOWN_EXTERNAL_ID


async def test_unknown_number_is_not_an_error(client):
    assert await client.find_customer_by_phone("+1-212-555-0000") is None
    assert await client.find_customer_by_phone("nonsense") is None


async def test_customer_carries_what_the_conversation_needs(client):
    customer = await client.find_customer_by_phone(KNOWN_PHONE)
    assert customer.full_name
    assert customer.plan
    # A Literal, not a class, so membership rather than isinstance.
    assert customer.status in get_args(AccountStatus)
    assert customer.address.count(",") >= 1


async def test_passcode_is_compared_and_never_returned(client):
    customer = await client.find_customer_by_phone(KNOWN_PHONE)
    assert await client.verify_passcode(customer.account_id, "0000") in (True, False)
    assert await client.verify_passcode(customer.account_id, "") is False
    # Nothing on the returned record exposes the stored value.
    assert "passcode" not in repr(customer).lower()


async def test_repeat_failure_history_is_visible(client):
    history = await client.get_case_history(REPEAT_FAILURE_EXTERNAL_ID)
    assert history.count >= 3
    device_id = history.cases[0].device_external_id
    assert history.is_repeat_failure(device_id)
    assert history.most_recent() is not None


async def test_household_without_history_returns_empty(client):
    history = await client.get_case_history("CUST-9999")
    assert history.count == 0
    assert history.most_recent() is None


async def test_token_is_acquired_once(client):
    await client.find_customer_by_phone(KNOWN_PHONE)
    first = client._token_acquired_at
    await client.find_customer_by_phone(KNOWN_PHONE)
    assert client._token_acquired_at == first
