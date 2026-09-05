"""Types describing the customer system's objects.

Generated from what that system publishes about its own fields. Do not edit;
run `python seed/gen_types.py` after changing them.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: F401
from typing import Literal

from pydantic import BaseModel

AccountStatus = Literal["Active", "Past Due", "Suspended", "Cancelled"]


class AccountRow(BaseModel):
    """The fields this project reads from a account."""

    Id: str | None = None
    Name: str
    Phone: str | None = None
    Plan__c: str | None = None
    Account_Status__c: AccountStatus | None = None
    Status_Since__c: date | None = None
    Wren_External_Id__c: str | None = None
    BillingStreet: str | None = None
    BillingCity: str | None = None
    BillingStateCode: str | None = None
    BillingPostalCode: str | None = None


class ContactRow(BaseModel):
    """The fields this project reads from a contact."""

    Id: str | None = None
    Name: str | None = None
    FirstName: str | None = None
    LastName: str
    Phone: str | None = None
    Email: str | None = None
    Wren_External_Id__c: str | None = None
    AccountId: str | None = None


class CaseRow(BaseModel):
    """The fields this project reads from a case."""

    Id: str | None = None
    CaseNumber: str | None = None
    Subject: str | None = None
    Description: str | None = None
    Status: str | None = None
    Origin: str | None = None
    Wren_External_Id__c: str | None = None
    Wren_Device_External_Id__c: str | None = None
    Wren_Occurred_On__c: date | None = None
