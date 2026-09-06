"""Client for the customer system.

Holds households, the people who call, their service plan and account status,
the passcode they use to prove who they are, and the history of what has gone
wrong before.

Calls are made concurrently with the telemetry lookup wherever possible: each
one costs a few hundred milliseconds, and a conversational turn has barely over
a second in total before a pause becomes audible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import date

import httpx

from .models import AccountStatus, CaseHistory, Customer, SupportCase

log = logging.getLogger(__name__)

API_VERSION = "v62.0"

# An access token lasts a couple of hours. It is refreshed well before that,
# because acquiring one costs about a second and a turn cannot spend it.
TOKEN_LIFETIME_SECONDS = 1800

# A lookup that has not answered by now will not arrive in time to be useful.
REQUEST_TIMEOUT_SECONDS = 5.0

# Acquiring a token is slower than any query, especially the first one on a
# cold connection, and it happens away from the conversation. It gets its own
# allowance so a caller's first turn is never the thing that pays for it.
TOKEN_TIMEOUT_SECONDS = 20.0

CUSTOMER_FIELDS = (
    "Id, FirstName, LastName, Name, Phone, Email, "
    "Account.Id, Account.Wren_External_Id__c, Account.Plan__c, "
    "Account.Account_Status__c, Account.Status_Since__c, "
    "Account.BillingStreet, Account.BillingCity, "
    "Account.BillingStateCode, Account.BillingPostalCode"
)


class SalesforceError(RuntimeError):
    """The customer system could not be reached or refused the request."""


def normalise_phone(spoken: str) -> str:
    """Reduce a phone number to digits.

    Callers read their number back in whatever shape they like, and speech
    recognition adds its own punctuation, so neither side of a comparison can be
    trusted to be formatted consistently.
    """
    digits = re.sub(r"\D", "", spoken or "")
    # A leading country code is optional in speech; drop it so both forms match.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


class SalesforceClient:
    def __init__(
        self,
        instance_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.instance_url = (instance_url or os.environ["SALESFORCE_INSTANCE_URL"]).rstrip("/")
        self._client_id = client_id or os.environ["SALESFORCE_CLIENT_ID"]
        self._client_secret = client_secret or os.environ["SALESFORCE_CLIENT_SECRET"]
        self._http = http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_http = http is None
        self._token: str | None = None
        self._token_acquired_at = 0.0
        self._token_lock = asyncio.Lock()

    async def warm(self) -> None:
        """Acquire the token before anyone calls.

        Without this the first caller of the day waits through a cold
        connection and a token request on top of their first lookup, which is
        several seconds and reads as the call having failed.
        """
        try:
            await self._access_token()
        except Exception as exc:  # noqa: BLE001 - a cold token is not fatal
            log.warning("could not acquire a token ahead of time: %s", exc)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---- authentication ----

    async def _access_token(self) -> str:
        if self._token and time.monotonic() - self._token_acquired_at < TOKEN_LIFETIME_SECONDS:
            return self._token
        # Several turns can discover an expired token at once; only one of them
        # should go and fetch a replacement.
        async with self._token_lock:
            if self._token and time.monotonic() - self._token_acquired_at < TOKEN_LIFETIME_SECONDS:
                return self._token
            response = await self._http.post(
                f"{self.instance_url}/services/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=TOKEN_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise SalesforceError(
                    f"could not authenticate: HTTP {response.status_code} {response.text[:200]}"
                )
            self._token = response.json()["access_token"]
            self._token_acquired_at = time.monotonic()
            log.info("acquired customer system access token")
            return self._token

    async def _query(self, soql: str) -> list[dict]:
        token = await self._access_token()
        response = await self._http.get(
            f"{self.instance_url}/services/data/{API_VERSION}/query",
            params={"q": soql},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            # The token was rejected; drop it and let the next call re-acquire.
            self._token = None
            raise SalesforceError("access token rejected")
        if response.status_code >= 400:
            raise SalesforceError(f"query failed: HTTP {response.status_code} {response.text[:200]}")
        return response.json().get("records", [])

    # ---- reads ----

    async def find_customer_by_phone(self, spoken_phone: str) -> Customer | None:
        """Look up the household behind a phone number.

        Returns None when nothing matches, which is an ordinary outcome rather
        than an error: people call from numbers that are not on their account.
        """
        digits = normalise_phone(spoken_phone)
        if len(digits) != 10:
            return None
        formatted = f"+1-{digits[0:3]}-{digits[3:6]}-{digits[6:]}"
        records = await self._query(
            f"SELECT {CUSTOMER_FIELDS} FROM Contact WHERE Phone = '{formatted}' LIMIT 1"
        )
        return _to_customer(records[0]) if records else None

    async def get_case_history(
        self, account_external_id: str, limit: int = 20
    ) -> CaseHistory:
        """What has gone wrong at this household before.

        Ordered most recent first, because the useful question is almost always
        whether this has happened lately rather than what happened years ago.
        """
        records = await self._query(
            "SELECT CaseNumber, Subject, Description, Status, "
            "Wren_Device_External_Id__c, Wren_Occurred_On__c FROM Case "
            f"WHERE Account.Wren_External_Id__c = '{account_external_id}' "
            f"ORDER BY Wren_Occurred_On__c DESC NULLS LAST LIMIT {limit}"
        )
        return CaseHistory(cases=[_to_case(r) for r in records])

    # ---- verification ----

    async def verify_passcode(self, account_id: str, spoken: str) -> bool:
        """Check a spoken passcode against the one on the account.

        The stored value is compared here and never returned. Anything this
        method handed back could reach the model, and anything the model has
        can be said out loud.
        """
        digits = re.sub(r"\D", "", spoken or "")
        if not digits:
            return False
        records = await self._query(
            f"SELECT Verbal_Passcode__c FROM Account WHERE Id = '{account_id}' LIMIT 1"
        )
        if not records:
            return False
        stored = (records[0].get("Verbal_Passcode__c") or "").strip()
        return bool(stored) and digits == stored


    # ---- writes ----

    async def create_case(
        self,
        account_id: str,
        contact_id: str,
        subject: str,
        description: str,
        device_external_id: str | None = None,
    ) -> str:
        """Open a support case and return the number to read back to the caller.

        The number matters: it is the only thing the caller leaves the call
        holding, so it has to come from the system rather than be invented here.
        """
        token = await self._access_token()
        response = await self._http.post(
            f"{self.instance_url}/services/data/{API_VERSION}/sobjects/Case",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "AccountId": account_id,
                "ContactId": contact_id,
                "Subject": subject[:255],
                "Description": description,
                "Status": "New",
                "Origin": "Phone",
                "Type": "Problem",
                "Wren_Device_External_Id__c": device_external_id,
            },
        )
        if response.status_code >= 400:
            raise SalesforceError(
                f"could not open a case: HTTP {response.status_code} {response.text[:200]}"
            )
        case_id = response.json()["id"]
        records = await self._query(f"SELECT CaseNumber FROM Case WHERE Id = '{case_id}'")
        return records[0]["CaseNumber"] if records else case_id

    async def escalate_case(self, case_number: str, reason: str) -> bool:
        """Mark a case for a person to pick up."""
        records = await self._query(
            f"SELECT Id, Description FROM Case WHERE CaseNumber = '{case_number}' LIMIT 1"
        )
        if not records:
            return False
        token = await self._access_token()
        existing = records[0].get("Description") or ""
        response = await self._http.patch(
            f"{self.instance_url}/services/data/{API_VERSION}/sobjects/Case/{records[0]['Id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "Status": "Escalated",
                "Priority": "High",
                "Description": f"{existing}\n\nEscalated: {reason}".strip(),
            },
        )
        if response.status_code >= 400:
            raise SalesforceError(
                f"could not escalate: HTTP {response.status_code} {response.text[:200]}"
            )
        return True


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _to_customer(record: dict) -> Customer:
    account = record.get("Account") or {}
    # No coercion. The status field is a restricted list, so the customer system
    # will not store a value outside it; whatever arrives is either one of them
    # or absent, and what an absent one means is decided in models.
    status: AccountStatus | None = account.get("Account_Status__c")
    return Customer(
        external_id=account.get("Wren_External_Id__c") or "",
        account_id=account.get("Id") or "",
        contact_id=record.get("Id") or "",
        full_name=record.get("Name") or "",
        first_name=record.get("FirstName") or "",
        phone=record.get("Phone") or "",
        email=record.get("Email") or "",
        plan=account.get("Plan__c") or "",
        status=status,
        status_since=_parse_date(account.get("Status_Since__c")),
        street=account.get("BillingStreet") or "",
        city=account.get("BillingCity") or "",
        state=account.get("BillingStateCode") or "",
        postal_code=account.get("BillingPostalCode") or "",
    )


def _to_case(record: dict) -> SupportCase:
    return SupportCase(
        case_number=record.get("CaseNumber") or "",
        subject=record.get("Subject") or "",
        description=record.get("Description") or "",
        status=record.get("Status") or "",
        device_external_id=record.get("Wren_Device_External_Id__c"),
        occurred_on=_parse_date(record.get("Wren_Occurred_On__c")),
    )
