"""Checks that logs carry enough to debug a call and not enough to leak one."""

from __future__ import annotations

import json
import logging

import pytest

from observability.logging import JsonFormatter, redact
from observability.timing import STAGE_BUDGETS_MS, StageTimer


@pytest.mark.parametrize("text,must_not_contain", [
    ("the passcode is 8241", "8241"),
    ("Passcode: 8 2 4 1", "8 2 4 1"),
    ("their pin is 8241 apparently", "8241"),
    ("code 8241", "8241"),
])
def test_a_passcode_never_survives_into_a_log(text, must_not_contain):
    assert must_not_contain not in redact(text)


def test_a_phone_number_is_recognisable_but_not_usable():
    out = redact("calling from +1-512-555-0135")
    assert "0135" in out, "two callers still have to be tellable apart"
    assert "512" not in out, "but the number must not be dialable"


def test_an_email_is_removed():
    assert "omar.lindqvist@example.com" not in redact("wrote to omar.lindqvist@example.com")


@pytest.mark.parametrize("field", ["passcode", "spoken", "client_secret", "api_key"])
def test_fields_that_never_belong_in_a_log_are_dropped(field):
    assert redact({field: "8241"})[field] == "[redacted]"


def test_redaction_reaches_into_nested_structures():
    out = redact({"tool": {"name": "check_passcode", "args": {"spoken": "8241"}}})
    assert out["tool"]["args"]["spoken"] == "[redacted]"


def test_ordinary_content_is_left_alone():
    text = "Take the cover off the sensor and wait about thirty seconds."
    assert redact(text) == text


def test_a_case_number_is_not_mistaken_for_a_passcode():
    assert "00001234" in redact("your case number is 00001234")


def test_a_log_line_is_json_carrying_its_fields():
    record = logging.LogRecord(
        "wren", logging.INFO, __file__, 1, "turn", None, None
    )
    record.fields = {"call_id": "CALL-abc", "ms_total": 812.4, "passcode": "8241"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["call_id"] == "CALL-abc"
    assert payload["ms_total"] == 812.4
    assert payload["passcode"] == "[redacted]"
    assert payload["level"] == "info"


# ---- timing ----

def test_a_stage_over_its_budget_is_named():
    timer = StageTimer()
    timer.record("first_token", STAGE_BUDGETS_MS["first_token"] + 200)
    timer.record("retrieval", 10)
    assert timer.timing.over_budget == ["first_token"]


def test_repeated_work_in_one_stage_adds_up():
    timer = StageTimer()
    timer.record("systems", 120)
    timer.record("systems", 130)
    assert timer.timing.stages["systems"] == 250


def test_rounds_are_counted_and_averaged():
    timer = StageTimer()
    for _ in range(3):
        timer.count_llm_round()
        timer.record("first_token", 600)
    fields = timer.timing.as_log_fields()
    assert fields["llm_rounds"] == 3
    assert fields["ms_first_token_per_round"] == 600.0


def test_only_stages_on_the_path_to_speaking_count_towards_the_total():
    timer = StageTimer()
    timer.record("first_token", 500)
    timer.record("something_alongside", 9999)
    assert timer.timing.total_ms == 500
