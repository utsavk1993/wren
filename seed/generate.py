"""Generate the customer and device records that populate the connected systems.

Everything here derives from a fixed random seed, so the same run produces the
same 200 customers and the same equipment on every machine. Reproducibility is
the point: scored test runs and demos need to start from an identical world, and
records are matched on the external identifier when loaded, so regenerating and
reloading updates rows in place rather than creating new ones.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

SEED = 20260904
CUSTOMER_COUNT = 200

FIRST_NAMES = [
    "Priya", "Marcus", "Dolores", "Terrance", "Jean", "Rosa", "Wesley", "Anita",
    "Curtis", "Yolanda", "Nathan", "Beatriz", "Omar", "Lorraine", "Desmond",
    "Ingrid", "Rafael", "Colleen", "Malik", "Sylvia", "Gordon", "Nadia",
    "Vincent", "Harriet", "Julius", "Marisol", "Preston", "Della", "Ibrahim",
    "Eleanor", "Sanjay", "Bernice", "Hugo", "Tamara", "Clyde", "Josefina",
]

LAST_NAMES = [
    "Raghunathan", "Bell", "Ng", "Okafor", "Aldridge", "Villanueva", "Hartley",
    "Sorensen", "Whitfield", "Castellanos", "Bergman", "Achebe", "Lindqvist",
    "Moreau", "Delacroix", "Yamamoto", "Brennan", "Kowalski", "Fitzgerald",
    "Oyelaran", "Marchetti", "Stavros", "Halvorsen", "Nakamura", "Boothby",
    "Ferreira", "Guillory", "Ashworth", "Petrov", "Sandoval", "Cavanaugh",
]

# Street names are paired with a city so an address never reads as impossible.
CITIES = [
    ("San Francisco", "CA", "94117", "415", ["Fell Street", "Guerrero Street", "Balboa Street"]),
    ("Austin", "TX", "78705", "512", ["Duval Street", "Rio Grande Street", "Manor Road"]),
    ("Seattle", "WA", "98107", "206", ["NW 60th Street", "Phinney Avenue", "Leary Way"]),
    ("Chicago", "IL", "60618", "312", ["W Belmont Avenue", "N Rockwell Street", "W Addison Street"]),
    ("Boston", "MA", "02114", "617", ["Bowdoin Street", "Cambridge Street", "Revere Street"]),
    ("Miami", "FL", "33135", "305", ["SW 8th Street", "NW 7th Avenue", "Calle Ocho"]),
    ("Denver", "CO", "80211", "303", ["Tejon Street", "W 32nd Avenue", "Zuni Street"]),
    ("Atlanta", "GA", "30307", "404", ["Moreland Avenue", "Euclid Avenue", "Dekalb Avenue"]),
]

PLANS = ["Essential", "Total Protection", "Total Protection Plus"]

# Each entry is the device type, the label a caller would use for it, and how
# many of that type a household plausibly has.
DEVICE_KINDS = [
    ("control_panel", ["Hallway Panel", "Front Entry Panel", "Main Panel", "Kitchen Panel"], (1, 1)),
    ("door_sensor", ["Front Door", "Back Door", "Side Door", "Garage Side Door", "Patio Door"], (2, 4)),
    ("window_sensor", ["Kitchen Window", "Living Room Window", "Bedroom Window",
                       "Upstairs Window", "Basement Window"], (2, 5)),
    ("motion_sensor", ["Living Room", "Hallway", "Basement", "Upstairs Landing", "Garage"], (2, 3)),
    ("camera", ["Driveway", "Front Porch", "Back Yard", "Side Yard", "Nursery"], (1, 3)),
    ("keypad", ["Upstairs Keypad", "Garage Keypad", "Back Entry Keypad"], (0, 1)),
]

# Most equipment is fine. These weights keep the fleet mostly healthy so that a
# caller reporting a problem is the exception rather than the norm.
STATUS_WEIGHTS = [("online", 88), ("offline", 7), ("low_battery", 5)]


@dataclass
class Customer:
    external_id: str
    first_name: str
    last_name: str
    phone: str
    email: str
    passcode: str
    street: str
    city: str
    state: str
    postal_code: str
    plan: str
    account_status: str = "Active"
    status_since: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass
class Case:
    external_id: str
    customer_external_id: str
    device_external_id: str
    subject: str
    description: str
    category: str
    resolution: str
    occurred_on: str


@dataclass
class Device:
    external_id: str
    customer_external_id: str
    name: str
    device_type: str
    status: str
    battery_pct: int | None
    last_seen: str
    recovers_on_reset: bool
    notes: str = ""


def _phone(rng: random.Random, area: str, used: set[str]) -> str:
    """Draw a number from the range reserved for fiction.

    555-0100 through 555-0199 will never route to a real subscriber, so no
    generated record can point at an actual person. That range holds only a
    hundred numbers per area code, which is why the cities carry different
    codes.
    """
    while True:
        candidate = f"+1-{area}-555-{rng.randint(100, 199):04d}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def _email(first: str, last: str, used: set[str]) -> str:
    """Build an address on the domain reserved for documentation.

    Nothing at example.com is deliverable, so no generated record can send mail
    to, or be confused with, a real person. Names repeat across the set, so a
    counter is appended only when one would otherwise collide.
    """
    base = f"{first}.{last}".lower()
    candidate = f"{base}@example.com"
    suffix = 2
    while candidate in used:
        candidate = f"{base}{suffix}@example.com"
        suffix += 1
    used.add(candidate)
    return candidate


def _weighted(rng: random.Random, choices: list[tuple[str, int]]) -> str:
    population = [c for c, _ in choices]
    weights = [w for _, w in choices]
    return rng.choices(population, weights=weights, k=1)[0]


# What a past incident looked like, keyed by the kind of equipment involved.
# The resolution text matters as much as the symptom: an agent reading history
# needs to know what was already tried, not just that something went wrong.
INCIDENTS = {
    "door_sensor": ("Sensor reporting offline", "sensor_offline",
                    "Power cycled the sensor and re-paired it to the panel. Came back online."),
    "window_sensor": ("Sensor reporting offline", "sensor_offline",
                      "Reseated the battery and re-paired. Restored."),
    "motion_sensor": ("False alarms overnight", "false_alarm",
                      "Reduced sensitivity and repositioned away from the vent. No further alerts."),
    "camera": ("Camera feed black", "camera_offline",
               "Restarted the camera and reconnected it to wifi. Feed restored."),
    "control_panel": ("Panel will not connect to wifi", "panel_wifi",
                      "Re-entered the network password and rebooted the panel. Reconnected."),
    "keypad": ("Keypad not responding", "keypad_unresponsive",
               "Replaced the batteries and re-paired the keypad."),
}

# How many households sit in each billing state. Monitoring only runs on an
# active account, so this is the first thing that has to be checked: a sensor
# on a suspended account is not being watched by anyone, and walking its owner
# through a repair would leave them believing they are protected when they are
# not.
STATUS_MIX = {
    "Active": 170,
    "Past Due": 15,
    "Suspended": 10,
    "Cancelled": 5,
}

# How long an account has been in its current state, in days.
STATUS_AGE_DAYS = {
    "Active": (120, 1500),
    "Past Due": (10, 60),
    "Suspended": (60, 180),
    "Cancelled": (90, 400),
}


# How many households fall into each history pattern. The point of the split is
# that history should usually be absent or irrelevant, so the cases where it
# genuinely changes the right answer stand out rather than being the norm.
HISTORY_MIX = {
    "none": 140,
    "unrelated": 40,
    "repeat_failure": 10,
    "recent": 10,
}


def _make_cases(
    rng: random.Random,
    customers: list[Customer],
    devices_by_customer: dict[str, list[Device]],
    now: datetime,
) -> list[Case]:
    """Build the incident history behind the customer base.

    Most households have none. A handful have the same piece of equipment fail
    over and over, which is the situation where walking the standard fix again
    is the wrong answer and the call should go to a human instead.
    """
    patterns = [p for p, count in HISTORY_MIX.items() for _ in range(count)]
    rng.shuffle(patterns)

    cases: list[Case] = []
    sequence = 1

    for customer, pattern in zip(customers, patterns):
        owned = devices_by_customer.get(customer.external_id, [])
        if pattern == "none" or not owned:
            continue

        if pattern == "repeat_failure":
            # One piece of equipment that keeps failing. Same symptom, same fix,
            # never actually cured. It is failing again now and will not come
            # back from another power cycle, so the only correct outcome is a
            # replacement rather than a fourth walk through the same steps.
            device = rng.choice(owned)
            device.status = "offline"
            device.recovers_on_reset = False
            device.notes = "Repeat failure. Prior resets have not held."
            occurrences = rng.randint(3, 4)
            days = sorted(rng.sample(range(30, 400), k=occurrences), reverse=True)
        elif pattern == "recent":
            # Serviced days ago and already failing again. A reset will work,
            # but repeating advice the caller just followed is the wrong opening
            # move.
            device = rng.choice(owned)
            device.status = "offline"
            device.recovers_on_reset = True
            occurrences = 1
            days = [rng.randint(2, 5)]
        else:
            device = rng.choice(owned)
            occurrences = 1
            days = [rng.randint(200, 540)]

        subject, category, resolution = INCIDENTS.get(
            device.device_type, ("Device fault reported", "other", "Resolved on the call.")
        )
        for day_offset in days:
            occurred = now - timedelta(days=day_offset)
            cases.append(Case(
                external_id=f"CASE-{3000 + sequence}",
                customer_external_id=customer.external_id,
                device_external_id=device.external_id,
                subject=f"{subject} - {device.name}",
                description=(
                    f"Caller reported the {device.name.lower()} "
                    f"({device.device_type.replace('_', ' ')}) was faulty."
                ),
                category=category,
                resolution=resolution,
                occurred_on=occurred.date().isoformat(),
            ))
            sequence += 1

    return cases


def _assign_statuses(
    rng: random.Random,
    customers: list[Customer],
    cases: list[Case],
    now: datetime,
) -> None:
    """Spread billing states across the customer base.

    Households whose equipment history is the point of the record are left
    active on purpose. Billing state is checked before anything else, so a
    suspended account never gets as far as its repair history, and putting the
    two on the same household would hide one behind the other.
    """
    case_counts: dict[str, int] = {}
    for case in cases:
        case_counts[case.customer_external_id] = case_counts.get(case.customer_external_id, 0) + 1

    recent_cutoff = (now - timedelta(days=14)).date().isoformat()
    protected = {
        case.customer_external_id
        for case in cases
        if case_counts[case.customer_external_id] >= 3 or case.occurred_on >= recent_cutoff
    }

    pool = [status for status, count in STATUS_MIX.items() for _ in range(count)]
    rng.shuffle(pool)

    # Protected households consume an Active slot so the totals still hold.
    remaining = list(pool)
    for customer in customers:
        if customer.external_id in protected:
            customer.account_status = "Active"
            if "Active" in remaining:
                remaining.remove("Active")

    for customer in customers:
        if customer.external_id not in protected:
            customer.account_status = remaining.pop() if remaining else "Active"

    for customer in customers:
        low, high = STATUS_AGE_DAYS[customer.account_status]
        customer.status_since = (now - timedelta(days=rng.randint(low, high))).date().isoformat()


def generate() -> tuple[list[Customer], list[Device], list[Case]]:
    rng = random.Random(SEED)
    now = datetime.now(timezone.utc)

    customers: list[Customer] = []
    devices: list[Device] = []
    used_phones: set[str] = set()
    used_emails: set[str] = set()
    device_seq = 1

    for i in range(CUSTOMER_COUNT):
        city, state, postal, area, streets = rng.choice(CITIES)
        first_name = rng.choice(FIRST_NAMES)
        last_name = rng.choice(LAST_NAMES)
        customer = Customer(
            external_id=f"CUST-{1001 + i}",
            first_name=first_name,
            last_name=last_name,
            phone=_phone(rng, area, used_phones),
            email=_email(first_name, last_name, used_emails),
            # Spoken aloud by the caller to prove they are the account holder.
            # The agent compares it and must never say it, so it stays four
            # digits: long enough to be a real check, short enough to say once.
            passcode=f"{rng.randint(0, 9999):04d}",
            street=f"{rng.randint(100, 4999)} {rng.choice(streets)}",
            city=city,
            state=state,
            postal_code=postal,
            plan=rng.choice(PLANS),
        )
        customers.append(customer)

        for device_type, labels, (low, high) in DEVICE_KINDS:
            count = rng.randint(low, high)
            for label in rng.sample(labels, k=min(count, len(labels))):
                status = _weighted(rng, STATUS_WEIGHTS)
                # Mains-powered equipment reports no battery level.
                battery = None if device_type in ("control_panel", "camera") else (
                    rng.randint(5, 19) if status == "low_battery" else rng.randint(35, 100)
                )
                minutes_ago = rng.randint(1, 10) if status == "online" else rng.randint(30, 4320)
                devices.append(Device(
                    external_id=f"DEV-{2000 + device_seq}",
                    customer_external_id=customer.external_id,
                    name=label,
                    device_type=device_type,
                    status=status,
                    battery_pct=battery,
                    last_seen=(now - timedelta(minutes=minutes_ago)).isoformat(),
                    # Most equipment recovers from a power cycle. The ones that
                    # do not are what force a call to reach a human.
                    recovers_on_reset=status == "online" or rng.random() < 0.75,
                ))
                device_seq += 1

    by_customer: dict[str, list[Device]] = {}
    for device in devices:
        by_customer.setdefault(device.customer_external_id, []).append(device)

    cases = _make_cases(rng, customers, by_customer, now)
    _assign_statuses(rng, customers, cases, now)
    return customers, devices, cases


if __name__ == "__main__":
    customers, devices, cases = generate()
    print(f"{len(customers)} customers, {len(devices)} devices")
    print(f"devices per customer: {len(devices) / len(customers):.1f}")
    from collections import Counter
    print("status mix:", dict(Counter(d.status for d in devices)))
    print("unrecoverable:", sum(1 for d in devices if not d.recovers_on_reset))
    print()
    print(f"{len(cases)} historical cases")
    per_customer = Counter(c.customer_external_id for c in cases)
    print("  households with history:", len(per_customer))
    print("  repeat offenders (3+ on one device):",
          sum(1 for n in per_customer.values() if n >= 3))
    print()
    print("first customer:", asdict(customers[0]))
