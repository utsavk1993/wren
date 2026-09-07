"""Checks that every rule can be deliberately tripped.

A guardrail nothing has ever hit is a guardrail nobody knows works, so each
test here puts the conversation into the exact situation the rule exists for.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from policy import (
    MAX_STEPS_BEFORE_HANDOFF,
    CallState,
    Denial,
    check_scope,
    detect_emergency,
    detect_request_for_a_person,
    find_leaked_passcode,
    find_unkeepable_promises,
    may_disclose_account_details,
    may_give_these_steps,
    may_troubleshoot,
    detect_wrapping_up,
    may_end_call,
    should_escalate,
)
from systems.models import AccountStatus, CaseHistory, Customer, Device, SupportCase


def make_customer(status="Active") -> Customer:
    return Customer(
        external_id="CUST-1001", account_id="001", contact_id="003",
        full_name="Priya Raghunathan", first_name="Priya", phone="+1-415-555-0142",
        email="p@example.com", plan="Total Protection", status=status,
        status_since=date(2026, 1, 1), street="1 Fell Street", city="San Francisco",
        state="CA", postal_code="94117",
    )


def make_device(recovers=True, status="offline") -> Device:
    return Device(
        external_id="DEV-2001", customer_external_id="CUST-1001", name="Front Door",
        device_type="door_sensor", status=status, battery_pct=68,
        last_seen=datetime(2026, 9, 1), recovers_on_reset=recovers, notes="",
        updated_at=datetime(2026, 9, 1),
    )


def history_for(device_id: str, count: int) -> CaseHistory:
    return CaseHistory(cases=[
        SupportCase(f"0000{i}", "Sensor offline", "", "Closed", device_id, date(2026, 1, i + 1))
        for i in range(count)
    ])


def verified_state(**kwargs) -> CallState:
    return CallState(customer=make_customer(), verified=True, **kwargs)


# ---- nothing is said before the caller proves who they are ----

def test_nothing_is_disclosed_before_anyone_is_identified():
    ruling = may_disclose_account_details(CallState())
    assert not ruling and ruling.reason is Denial.NOT_VERIFIED


def test_nothing_is_disclosed_after_identification_but_before_verification():
    ruling = may_disclose_account_details(CallState(customer=make_customer()))
    assert not ruling and ruling.reason is Denial.NOT_VERIFIED


def test_two_failed_attempts_ends_it():
    state = CallState(customer=make_customer(), verification_attempts=2)
    ruling = may_disclose_account_details(state)
    assert not ruling and ruling.reason is Denial.VERIFICATION_FAILED
    assert "callback" in ruling.guidance.lower()


def test_a_verified_caller_may_be_told_about_their_account():
    assert may_disclose_account_details(verified_state())


# ---- unmonitored equipment is not repaired ----

@pytest.mark.parametrize("status", ["Suspended", "Cancelled"])
def test_unmonitored_accounts_are_not_troubleshot(status):
    state = CallState(customer=make_customer(status), verified=True)
    ruling = may_troubleshoot(state)
    assert not ruling and ruling.reason is Denial.NOT_MONITORED
    assert "not being monitored" in ruling.guidance


@pytest.mark.parametrize("status", ["Active", "Past Due"])
def test_monitored_accounts_are_troubleshot(status):
    assert may_troubleshoot(CallState(customer=make_customer(status), verified=True))


def test_an_overdue_account_is_never_told_about_money():
    state = CallState(customer=make_customer("Past Due"), verified=True)
    ruling = may_troubleshoot(state)
    assert ruling, "an overdue account is still monitored and should be helped"


# ---- equipment that keeps failing is replaced, not repaired again ----

def test_repeat_failure_stops_the_same_repair_being_offered_again():
    device = make_device()
    state = verified_state(
        device_under_discussion=device, history=history_for(device.external_id, 3)
    )
    ruling = may_troubleshoot(state)
    assert not ruling and ruling.reason is Denial.REPEAT_FAILURE
    assert "replacing" in ruling.guidance


def test_history_about_other_equipment_does_not_block_a_repair():
    state = verified_state(
        device_under_discussion=make_device(), history=history_for("DEV-9999", 5)
    )
    assert may_troubleshoot(state)


def test_equipment_that_will_not_come_back_stops_further_steps():
    state = verified_state(device_under_discussion=make_device(recovers=False),
                           steps_given=["reseat the battery"])
    ruling = may_troubleshoot(state)
    assert not ruling and ruling.reason is Denial.REPEAT_FAILURE


def test_enough_attempts_ends_the_call():
    state = verified_state(steps_given=[f"step {i}" for i in range(MAX_STEPS_BEFORE_HANDOFF)])
    ruling = may_troubleshoot(state)
    assert not ruling and ruling.reason is Denial.STEPS_EXHAUSTED


# ---- no steps without something to base them on ----

def test_no_steps_are_given_when_nothing_was_retrieved():
    ruling = may_give_these_steps(verified_state(), [])
    assert not ruling and ruling.reason is Denial.NO_GROUNDING
    assert "do not improvise" in ruling.guidance.lower()


def test_steps_are_allowed_when_something_was_retrieved():
    assert may_give_these_steps(verified_state(), ["1. Take the cover off."])


def test_grounding_does_not_override_an_unmonitored_account():
    state = CallState(customer=make_customer("Suspended"), verified=True)
    ruling = may_give_these_steps(state, ["1. Take the cover off."])
    assert ruling.reason is Denial.NOT_MONITORED


# ---- staying on topic ----

@pytest.mark.parametrize("utterance", [
    "I want to cancel my subscription",
    "why was I charged twice this month",
    "how much does a new sensor cost",
    "can I add my daughter to the account",
    "I'd like to reactivate my service",
    "I'm moving house next month",
])
def test_questions_that_are_not_about_equipment_are_refused(utterance):
    ruling = check_scope(utterance)
    assert not ruling and ruling.reason is Denial.OUT_OF_SCOPE


@pytest.mark.parametrize("utterance", [
    "my door sensor is offline",
    "the camera shows a black screen",
    "the panel keeps beeping",
    "how do I arm the system",
])
def test_equipment_questions_are_allowed(utterance):
    assert check_scope(utterance)


# ---- things that stop the call ----

@pytest.mark.parametrize("utterance", [
    "there's a fire in the kitchen",
    "someone is in my house right now",
    "my husband is hurt, he's bleeding",
    "I think there's an intruder downstairs",
])
def test_emergencies_are_recognised(utterance):
    assert detect_emergency(utterance)


def test_ordinary_faults_are_not_mistaken_for_emergencies():
    assert not detect_emergency("the smoke detector keeps chirping")
    assert not detect_emergency("my front door sensor is offline")


@pytest.mark.parametrize("utterance", [
    "can I speak to a real person",
    "put me through to someone",
    "I want to talk to a human",
    "transfer me to a manager",
])
def test_a_request_for_a_person_is_recognised(utterance):
    assert detect_request_for_a_person(utterance)


def test_a_request_for_a_person_ends_troubleshooting_immediately():
    state = verified_state(caller_requested_human=True)
    ruling = may_troubleshoot(state)
    assert not ruling and ruling.reason is Denial.CALLER_ASKED_FOR_A_PERSON
    assert "without making them justify" in ruling.guidance


@pytest.mark.parametrize("utterance", [
    "okay, goodbye",
    "thanks, bye",
    "you can hang up now",
    "that's all I needed",
    "that's everything",
    "nothing else, thanks",
    "I'm all set",
    "we're done here",
    "have a good one",
])
def test_a_caller_ending_the_call_is_recognised(utterance):
    assert detect_wrapping_up(utterance)


@pytest.mark.parametrize("utterance", [
    "thanks for that",
    "great, that worked",
    "okay, what's next",
    "so I hold the button down",
    "it's all flashing now",
    "that's the one that's broken",
    "that's it, the light is on now",
])
def test_being_partway_through_is_not_mistaken_for_leaving(utterance):
    """Hanging up on someone mid-repair is far worse than staying on the line.

    Everything here is said in the middle of a call, often right after a step
    has worked, which is exactly when a caller sounds like they are finishing
    and is not.
    """
    assert not detect_wrapping_up(utterance)


def test_an_emergency_outranks_everything():
    state = verified_state(emergency_declared=True)
    assert should_escalate(state).reason is Denial.EMERGENCY
    assert may_troubleshoot(state).reason is Denial.EMERGENCY


# ---- ending the call ----

def test_a_call_cannot_end_before_anything_is_settled():
    """Hanging up is not a way out of a question.

    The model decides when a conversation is finished, but not that it is
    finished simply because it has run out of ideas. That is what opening a
    case and handing over are for.
    """
    ruling = may_end_call(verified_state())
    assert not ruling and ruling.reason is Denial.NOTHING_CONCLUDED
    assert "have not answered" in ruling.guidance


@pytest.mark.parametrize("reached", [
    {"fault_resolved": True},
    {"case_number": "00123456"},
    {"caller_requested_human": True},
    {"caller_said_goodbye": True},
    {"emergency_declared": True},
])
def test_a_call_may_end_once_the_caller_knows_what_happens_next(reached):
    assert may_end_call(verified_state(**reached))


def test_an_unidentified_caller_saying_goodbye_may_still_leave():
    """Ending a call needs no account. Anyone may hang up."""
    assert may_end_call(CallState(caller_said_goodbye=True))


# ---- what the agent is allowed to say ----

@pytest.mark.parametrize("reply", [
    "A technician will be there within 2 hours.",
    "Someone will call you by tomorrow.",
    "I'll schedule an engineer for you.",
    "We'll waive the fee for that.",
    "That replacement is free of charge.",
    "I guarantee this will fix it.",
])
def test_commitments_the_agent_cannot_keep_are_caught(reply):
    assert find_unkeepable_promises(reply)


@pytest.mark.parametrize("reply", [
    "A member of the team will call you back on the number on your account.",
    "I've opened a case for you, the number is 00001234.",
    "Take the cover off the sensor and remove the battery for about ten seconds.",
])
def test_honest_replies_are_not_flagged(reply):
    assert not find_unkeepable_promises(reply)


def test_a_spoken_passcode_is_caught():
    assert find_leaked_passcode("Your passcode is 8241.", "8241")
    # Read out the way a person says a code aloud.
    assert find_leaked_passcode("That's eight, two, 8 2 4 1, yes.", "8241")


def test_unrelated_numbers_are_not_mistaken_for_the_passcode():
    assert not find_leaked_passcode("Your case number is 00001109.", "8241")
    assert not find_leaked_passcode("Wait about thirty seconds.", "8241")
    assert not find_leaked_passcode("Anything at all.", None)
