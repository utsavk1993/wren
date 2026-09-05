"""Shapes returned by the system clients.

The orchestrator works with these rather than raw API payloads, so a change in
how a connected system names its fields does not reach the conversation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


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


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    LOW_BATTERY = "low_battery"


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
class Device:
    external_id: str
    customer_external_id: str
    name: str
    device_type: str
    status: DeviceStatus
    battery_pct: int | None
    last_seen: datetime | None
    recovers_on_reset: bool
    notes: str = ""

    @property
    def is_faulty(self) -> bool:
        return self.status is not DeviceStatus.ONLINE


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
