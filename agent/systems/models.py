"""Shapes returned by the system clients.

The device shapes are generated from the telemetry platform's own description of
its tables and re-exported here, so the values a status may take come from the
database rather than from someone keeping two lists in step. The customer system
publishes no types, so those are written out below.

The orchestrator works with these rather than raw API payloads, so a change in
how a connected system names its fields does not reach the conversation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from .generated import Device, DeviceKind, DeviceStatus

__all__ = [
    "AccountStatus",
    "CaseHistory",
    "Customer",
    "Device",
    "DeviceKind",
    "DeviceStatus",
    "SupportCase",
    "is_faulty",
]

class AccountStatus(str, Enum):
    ACTIVE = "Active"
    PAST_DUE = "Past Due"
    SUSPENDED = "Suspended"
    CANCELLED = "Cancelled"

    @property
    def is_monitored(self) -> bool:
        """Whether the alarm signals from this household are being watched.

        A suspended or closed account has no monitoring behind it. Repairing
        equipment for one of these callers would leave them believing they are
        protected when nobody is listening, so this decides whether
        troubleshooting should happen at all.
        """
        return self in (AccountStatus.ACTIVE, AccountStatus.PAST_DUE)


ONLINE: DeviceStatus = "online"


def is_faulty(device: Device) -> bool:
    """Whether this equipment is failing to report.

    A free function rather than something on the record, because the record is
    generated from the database and nothing here should shadow it.
    """
    return device.status != ONLINE


@dataclass(frozen=True)
class Customer:
    external_id: str
    account_id: str
    contact_id: str
    full_name: str
    first_name: str
    phone: str
    email: str
    plan: str
    status: AccountStatus
    status_since: date | None
    street: str
    city: str
    state: str
    postal_code: str

    @property
    def address(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.postal_code}"


@dataclass(frozen=True)
class SupportCase:
    case_number: str
    subject: str
    description: str
    status: str
    device_external_id: str | None
    occurred_on: date | None

    @property
    def is_open(self) -> bool:
        return self.status not in ("Closed", "Resolved")


@dataclass(frozen=True)
class CaseHistory:
    """What has already been tried on a piece of equipment.

    The counts matter more than the individual records: equipment that has been
    repaired several times and has failed again does not need the same repair
    explained a fourth time.
    """

    cases: list[SupportCase] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.cases)

    def for_device(self, device_external_id: str) -> list[SupportCase]:
        return [c for c in self.cases if c.device_external_id == device_external_id]

    def is_repeat_failure(self, device_external_id: str, threshold: int = 2) -> bool:
        return len(self.for_device(device_external_id)) >= threshold

    def most_recent(self) -> SupportCase | None:
        dated = [c for c in self.cases if c.occurred_on]
        return max(dated, key=lambda c: c.occurred_on) if dated else None
