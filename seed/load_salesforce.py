"""Load the customer base into Salesforce.

Households become Accounts and the person who answers the phone becomes a
Contact. Records are matched on an external identifier that this project owns
rather than on the identifier Salesforce assigns, because Salesforce IDs differ
between orgs; keying on ours means the same load can run against any org and
still update the right rows.

Writes go through the composite collections endpoint, which accepts 200 records
per request, so the whole customer base costs a handful of API calls rather than
one per record.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from generate import Case, Customer, generate

log = logging.getLogger(__name__)

API_VERSION = "v62.0"
BATCH_SIZE = 200
EXTERNAL_ID_FIELD = "Wren_External_Id__c"

# Records for real people are kept out of version control. The file is optional;
# when present its entries replace generated ones with the same external id.
REAL_CUSTOMERS_PATH = Path(__file__).parent / "data" / "real_customers.json"


class Salesforce:
    """Thin REST client using the OAuth client credentials flow.

    The access token is cached because acquiring one costs about a second, which
    is more than a conversational turn can afford to spend.
    """

    def __init__(self) -> None:
        self.instance = os.environ["SALESFORCE_INSTANCE_URL"].rstrip("/")
        self.client_id = os.environ["SALESFORCE_CLIENT_ID"]
        self.client_secret = os.environ["SALESFORCE_CLIENT_SECRET"]
        self._token: str | None = None
        self._token_acquired_at = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() - self._token_acquired_at < 1800:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        with urllib.request.urlopen(
            urllib.request.Request(f"{self.instance}/services/oauth2/token", data=body)
        ) as response:
            payload = json.load(response)
        self._token = payload["access_token"]
        self._token_acquired_at = time.time()
        return self._token

    def request(self, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{self.instance}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._access_token()}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw.decode()[:500]


def _account_payload(customer: Customer) -> dict[str, Any]:
    return {
        "attributes": {"type": "Account"},
        EXTERNAL_ID_FIELD: customer.external_id,
        "Name": f"{customer.full_name} Residence",
        "Plan__c": customer.plan,
        "Verbal_Passcode__c": customer.passcode,
        "Account_Status__c": customer.account_status,
        "Status_Since__c": customer.status_since,
        "Phone": customer.phone,
        "BillingStreet": customer.street,
        "BillingCity": customer.city,
        "BillingPostalCode": customer.postal_code,
        # The org has state and country picklists switched on, so these have to
        # be the ISO codes rather than free text.
        "BillingStateCode": customer.state,
        "BillingCountryCode": "US",
    }


def _contact_payload(customer: Customer, account_id: str) -> dict[str, Any]:
    return {
        "attributes": {"type": "Contact"},
        EXTERNAL_ID_FIELD: customer.external_id,
        "FirstName": customer.first_name,
        "LastName": customer.last_name,
        "Phone": customer.phone,
        "Email": customer.email,
        "AccountId": account_id,
        "MailingStreet": customer.street,
        "MailingCity": customer.city,
        "MailingPostalCode": customer.postal_code,
        "MailingStateCode": customer.state,
        "MailingCountryCode": "US",
    }


def _case_payload(case: Case, account_id: str, contact_id: str) -> dict[str, Any]:
    return {
        "attributes": {"type": "Case"},
        EXTERNAL_ID_FIELD: case.external_id,
        "AccountId": account_id,
        "ContactId": contact_id,
        "Subject": case.subject,
        "Description": f"{case.description}\n\nResolution: {case.resolution}",
        "Status": "Closed",
        "Origin": "Phone",
        "Type": "Problem",
        "Wren_Device_External_Id__c": case.device_external_id,
        "Wren_Occurred_On__c": case.occurred_on,
    }


def _batched(items: list[Any], size: int = BATCH_SIZE):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _upsert(sf: Salesforce, sobject: str, records: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Upsert records in batches, returning how many succeeded and any errors."""
    succeeded = 0
    errors: list[str] = []
    for batch in _batched(records):
        status, body = sf.request(
            "PATCH",
            f"/services/data/{API_VERSION}/composite/sobjects/{sobject}/{EXTERNAL_ID_FIELD}",
            {"allOrNone": False, "records": batch},
        )
        if status >= 400 or not isinstance(body, list):
            errors.append(f"batch failed with HTTP {status}: {str(body)[:300]}")
            continue
        for result in body:
            if result.get("success"):
                succeeded += 1
            else:
                errors.append(str(result.get("errors"))[:300])
    return succeeded, errors


def _apply_real_customers(customers: list[Customer]) -> list[Customer]:
    """Overlay records for real people, if the untracked file is present."""
    if not REAL_CUSTOMERS_PATH.exists():
        return customers
    overrides = {entry["external_id"]: entry for entry in json.loads(REAL_CUSTOMERS_PATH.read_text())}
    merged = []
    for customer in customers:
        override = overrides.pop(customer.external_id, None)
        merged.append(Customer(**{**customer.__dict__, **override}) if override else customer)
    if overrides:
        raise ValueError(f"real_customers.json references unknown ids: {sorted(overrides)}")
    log.info("applied %d real customer records", len(customers) - sum(
        1 for c, m in zip(customers, merged) if c is m))
    return merged


def load() -> dict[str, Any]:
    customers, _, cases = generate()
    customers = _apply_real_customers(customers)
    sf = Salesforce()

    accounts_ok, account_errors = _upsert(
        sf, "Account", [_account_payload(c) for c in customers]
    )
    if account_errors:
        raise RuntimeError(
            f"{len(account_errors)} account upserts failed, first: {account_errors[0]}"
        )

    # Contacts carry a lookup to their Account, so the ids have to be read back
    # before they can be written.
    account_ids = _fetch_ids(sf, "Account")
    missing = [c.external_id for c in customers if c.external_id not in account_ids]
    if missing:
        raise RuntimeError(f"{len(missing)} accounts missing after upsert, e.g. {missing[:3]}")

    contacts_ok, contact_errors = _upsert(
        sf, "Contact", [_contact_payload(c, account_ids[c.external_id]) for c in customers]
    )

    if contact_errors:
        raise RuntimeError(
            f"{len(contact_errors)} contact upserts failed, first: {contact_errors[0]}"
        )

    # Cases point at both the household and the person, so both sets of ids have
    # to exist before any incident history can be written.
    contact_ids = _fetch_ids(sf, "Contact")
    cases_ok, case_errors = _upsert(sf, "Case", [
        _case_payload(c, account_ids[c.customer_external_id], contact_ids[c.customer_external_id])
        for c in cases
    ])

    return {
        "customers": len(customers),
        "accounts_upserted": accounts_ok,
        "contacts_upserted": contacts_ok,
        "cases_upserted": cases_ok,
        "errors": account_errors + contact_errors + case_errors,
    }


def _fetch_ids(sf: Salesforce, sobject: str) -> dict[str, str]:
    """Map our external identifiers to the ids Salesforce assigned."""
    ids: dict[str, str] = {}
    query = f"SELECT Id, {EXTERNAL_ID_FIELD} FROM {sobject} WHERE {EXTERNAL_ID_FIELD} != null"
    path = f"/services/data/{API_VERSION}/query?q={urllib.parse.quote(query)}"
    while path:
        status, body = sf.request("GET", path)
        if status >= 400:
            raise RuntimeError(f"{sobject} lookup failed with HTTP {status}: {body}")
        for record in body["records"]:
            ids[record[EXTERNAL_ID_FIELD]] = record["Id"]
        path = body.get("nextRecordsUrl")
    return ids


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = load()
    print(json.dumps({k: v for k, v in result.items() if k != "errors"}, indent=2))
    if result["errors"]:
        print(f"\n{len(result['errors'])} errors, first few:")
        for message in result["errors"][:5]:
            print(f"  {message}")
