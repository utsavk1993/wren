"""Checks that what a tester is told matches the data that was loaded.

The numbers and passcodes on the page belong to generated households. If the
generator changes and the page does not, the first anyone hears is a tester
being told their account does not exist, halfway through trying the thing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "seed"))

PUBLISHED = ROOT / "web" / "src" / "scenarios.json"


@pytest.fixture(scope="module")
def published() -> list[dict]:
    return json.loads(PUBLISHED.read_text())


@pytest.fixture(scope="module")
def freshly_built() -> list[dict]:
    from scenarios import build

    return build()


def test_the_page_matches_the_data(published, freshly_built):
    assert published == freshly_built, (
        "the scenarios shown to testers no longer match the seeded data; "
        "run `python seed/scenarios.py`"
    )


def test_every_case_can_actually_be_carried_out(published):
    for case in published:
        assert case["phone"], case["title"]
        assert case["passcode"].isdigit() and len(case["passcode"]) == 4, case["title"]
        assert case["lines"], case["title"]
        # Without this a tester cannot tell a correct refusal from a failure,
        # which is the entire point of the second and third cases.
        assert case["expect"], case["title"]


def test_the_credentials_belong_to_real_households(published):
    from generate import generate

    customers, _, _ = generate()
    by_digits = {
        "".join(c for c in customer.phone if c.isdigit())[-10:]: customer
        for customer in customers
    }
    for case in published:
        digits = "".join(c for c in case["phone"] if c.isdigit())
        customer = by_digits.get(digits)
        assert customer is not None, f"{case['title']}: no household on {case['phone']}"
        assert customer.passcode == case["passcode"], f"{case['title']}: wrong passcode"
        assert customer.full_name == case["name"], f"{case['title']}: wrong name"


def test_the_three_cases_are_actually_different(published):
    """Three variations on the same call would teach a tester nothing.

    One should be repaired, one is beyond repairing, and one must not be
    touched at all.
    """
    from collections import Counter

    from generate import generate

    customers, devices, cases = generate()
    by_phone = {
        "".join(c for c in cu.phone if c.isdigit())[-10:]: cu for cu in customers
    }
    history = Counter(c.customer_external_id for c in cases)

    def household(case):
        return by_phone["".join(c for c in case["phone"] if c.isdigit())]

    ordinary, repeat, suspended = (household(c) for c in published)

    assert ordinary.account_status == "Active"
    assert not history.get(ordinary.external_id), "the ordinary case should have no history"

    assert repeat.account_status == "Active"
    assert history.get(repeat.external_id, 0) >= 3, "the repeat case needs prior failures"

    assert suspended.account_status == "Suspended", "the third case must not be monitored"
