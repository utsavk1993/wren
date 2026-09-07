"""The cases a tester is given, taken from the data that was actually loaded.

Someone trying this has to be handed a phone number, a passcode and something to
say, because none of it can be guessed. Written into the page by hand those
would drift the moment the generator changed, and the first anyone would know is
a tester being told their account does not exist.

So they are derived here from the same generator that filled the two systems,
and written to a file the page reads. A test compares the two.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from generate import generate

OUTPUT = Path(__file__).resolve().parents[1] / "web" / "src" / "scenarios.json"


def _phone_as_spoken(phone: str) -> str:
    """Written the way it should be read out, not the way it is stored."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}"


def build() -> list[dict]:
    customers, devices, cases = generate()
    by_id = {c.external_id: c for c in customers}
    case_count = Counter(c.customer_external_id for c in cases)
    owned: dict[str, list] = {}
    for device in devices:
        owned.setdefault(device.customer_external_id, []).append(device)

    # An ordinary fault: active account, no history, something broken that a
    # reset will fix.
    ordinary = next(
        (c, next(d for d in owned[c.external_id]
                 if d.status != "online" and d.recovers_on_reset))
        for c in customers
        if c.account_status == "Active"
        and not case_count.get(c.external_id)
        and any(d.status != "online" and d.recovers_on_reset
                for d in owned.get(c.external_id, []))
    )

    # Equipment that has failed repeatedly and is broken again.
    repeat_id = next(cid for cid, n in case_count.items() if n >= 3)
    repeat_device_id = next(
        c.device_external_id for c in cases if c.customer_external_id == repeat_id
    )
    repeat = (
        by_id[repeat_id],
        next(d for d in owned[repeat_id] if d.external_id == repeat_device_id),
    )

    # An account nobody is monitoring.
    suspended_customer = next(c for c in customers if c.account_status == "Suspended")
    suspended = (
        suspended_customer,
        next(d for d in owned[suspended_customer.external_id] if d.status != "online"),
    )

    def opening(device) -> str:
        kind = device.device_type.replace("_", " ")
        if device.status == "low_battery":
            return f"my {device.name.lower()} {kind} is showing a low battery"
        return f"my {device.name.lower()} {kind} is offline"

    ordinary_customer, ordinary_device = ordinary
    repeat_customer, repeat_device = repeat
    suspended_customer, suspended_device = suspended

    return [
        {
            "id": "ordinary",
            "title": "A fault it can fix",
            "why": "The everyday call. It should find the steps and walk you through them.",
            "phone": _phone_as_spoken(ordinary_customer.phone),
            "passcode": ordinary_customer.passcode,
            "name": ordinary_customer.full_name,
            "lines": [
                "hi, my sensor isn't working",
                _phone_as_spoken(ordinary_customer.phone),
                ordinary_customer.passcode,
                opening(ordinary_device),
                "okay, I've done that",
            ],
            "expect": [
                "Greets you back rather than ignoring you",
                "Asks for the number, then the passcode",
                f"Finds the {ordinary_device.name}",
                "Gives one step at a time and waits for you",
            ],
        },
        {
            "id": "repeat",
            "title": "Equipment that keeps failing",
            "why": (
                "This has been repaired three times already and is broken again. "
                "Walking you through the same fix a fourth time is the wrong answer."
            ),
            "phone": _phone_as_spoken(repeat_customer.phone),
            "passcode": repeat_customer.passcode,
            "name": repeat_customer.full_name,
            "lines": [
                _phone_as_spoken(repeat_customer.phone),
                repeat_customer.passcode,
                opening(repeat_device),
            ],
            "expect": [
                "Notices it has been out before",
                "Does not repeat the same repair",
                "Says it needs replacing and gets you a person",
            ],
        },
        {
            "id": "suspended",
            "title": "An account nobody is watching",
            "why": (
                "Monitoring has stopped on this account. Fixing the sensor would "
                "leave you believing you are protected when nobody is listening, "
                "so it should refuse to troubleshoot at all."
            ),
            "phone": _phone_as_spoken(suspended_customer.phone),
            "passcode": suspended_customer.passcode,
            "name": suspended_customer.full_name,
            "lines": [
                _phone_as_spoken(suspended_customer.phone),
                suspended_customer.passcode,
                opening(suspended_device),
                "can you just walk me through fixing it anyway",
            ],
            "expect": [
                "Says plainly that monitoring is not active",
                "Refuses to troubleshoot, even when pushed",
                "Offers the team who can restart the service",
            ],
        },
    ]


if __name__ == "__main__":
    scenarios = build()
    OUTPUT.write_text(json.dumps(scenarios, indent=2) + "\n")
    print(f"wrote {OUTPUT}")
    for s in scenarios:
        print(f"  {s['title']}: {s['phone']} / {s['passcode']} ({s['name']})")
