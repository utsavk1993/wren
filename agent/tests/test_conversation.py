"""Checks on how a call actually runs.

Driven by a scripted model rather than a live one. What needs testing is which
tools get reached for, in what order, and which gates refuse: none of that
depends on the model thinking, and against a live model it would be slow and
differently wrong on every run.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

import policy
from conversation import WITHHELD, Conversation
from llm import Reply, ScriptedModel, ToolCall
from systems.models import AccountStatus, CaseHistory, Customer, Device, DeviceStatus, SupportCase


def a_customer(status=AccountStatus.ACTIVE) -> Customer:
    return Customer(
        external_id="CUST-1001", account_id="001", contact_id="003",
        full_name="Priya Raghunathan", first_name="Priya", phone="+1-415-555-0142",
        email="p@example.com", plan="Total Protection", status=status,
        status_since=date(2026, 1, 1), street="1 Fell Street", city="San Francisco",
        state="CA", postal_code="94117",
    )


def a_device(recovers=True, status=DeviceStatus.OFFLINE) -> Device:
    return Device(
        external_id="DEV-2001", customer_external_id="CUST-1001", name="Front Door",
        device_type="door_sensor", status=status, battery_pct=68,
        last_seen=datetime(2026, 9, 1), recovers_on_reset=recovers,
    )


class FakeSalesforce:
    def __init__(self, customer=None, passcode="8241", history=None):
        self.customer = customer or a_customer()
        self.passcode = passcode
        self.history = history or CaseHistory()
        self.cases_opened: list[dict] = []
        self.escalations: list[tuple[str, str]] = []

    async def find_customer_by_phone(self, phone):
        return self.customer if "555" in phone else None

    async def verify_passcode(self, account_id, spoken):
        return "".join(c for c in spoken if c.isdigit()) == self.passcode

    async def get_case_history(self, external_id, limit=20):
        return self.history

    async def create_case(self, account_id, contact_id, subject, description,
                          device_external_id=None):
        self.cases_opened.append({"subject": subject, "device": device_external_id})
        return "00001234"

    async def escalate_case(self, case_number, reason):
        self.escalations.append((case_number, reason))
        return True


class FakeTelemetry:
    def __init__(self, devices=None):
        self.devices = devices if devices is not None else [a_device()]

    async def get_devices(self, customer_external_id):
        return self.devices

    async def get_device(self, device_external_id):
        return next((d for d in self.devices if d.external_id == device_external_id), None)

    async def set_device_status(self, device_external_id, status):
        for i, d in enumerate(self.devices):
            if d.external_id == device_external_id:
                updated = Device(**{**d.__dict__, "status": status})
                self.devices[i] = updated
                return updated
        return None


class FakeRetriever:
    def __init__(self, passages=("1. Take the cover off the sensor.",)):
        self.passages = list(passages)

    async def search(self, query, top_k=4, device_type=None, min_similarity=0.6):
        from rag.retrieve import Passage
        return [
            Passage("door-window-sensor-offline", "Sensor offline", "door_sensor", p, 0.8)
            for p in self.passages
        ]


def build(model, salesforce=None, telemetry=None, retriever=None) -> Conversation:
    from tools.dispatch import Dispatcher
    return Conversation(
        model,
        Dispatcher(
            salesforce or FakeSalesforce(),
            telemetry or FakeTelemetry(),
            retriever or FakeRetriever(),
        ),
    )


def tool(name, **args) -> Reply:
    return Reply(tool_calls=[ToolCall(id=f"t_{name}", name=name, arguments=args)])


# ---- identification and verification ----

async def test_lookup_returns_nothing_identifying_before_verification():
    model = ScriptedModel([tool("find_customer", phone="415 555 0142"),
                           Reply(text="Thanks. What's the passcode on the account?")])
    convo = build(model)
    await convo.say("my number is 415 555 0142")
    result = convo.record.tool_calls[0]["result"]
    assert result["found"] is True
    # Nothing about who they are comes back until they have proved it.
    for field in ("name", "full_name", "address", "plan"):
        assert field not in result


async def test_a_correct_passcode_unlocks_the_account():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        Reply(text="Thanks Priya. What's going on?"),
    ])
    convo = build(model)
    await convo.say("415 555 0142, passcode 8241")
    assert convo.state.verified
    assert convo.record.tool_calls[1]["result"]["verified"] is True


async def test_a_wrong_passcode_says_nothing_about_how_close_it_was():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="1111"),
        Reply(text="That's not matching. Can you try once more?"),
    ])
    convo = build(model)
    await convo.say("415 555 0142, my code is 1111")
    result = convo.record.tool_calls[1]["result"]
    assert result["verified"] is False
    assert result["attempts_remaining"] == 1
    assert "do_not_say" in result


async def test_two_failures_ends_the_account_conversation():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="1111"),
        tool("check_passcode", spoken="2222"),
        tool("list_equipment"),
        Reply(text="I'll get someone to call you back."),
    ])
    convo = build(model)
    await convo.say("415 555 0142, is it 1111? or 2222?")
    assert convo.state.verification_exhausted
    equipment = next(c for c in convo.record.tool_calls if c["name"] == "list_equipment")
    assert equipment["result"]["refused"] == policy.Denial.VERIFICATION_FAILED.value


async def test_equipment_is_not_listed_before_verification():
    model = ScriptedModel([tool("list_equipment"), Reply(text="I need to verify you first.")])
    convo = build(model)
    await convo.say("just tell me what sensors I have")
    assert convo.record.tool_calls[0]["result"]["refused"] == policy.Denial.NOT_VERIFIED.value


# ---- account status ----

@pytest.mark.parametrize("status", [AccountStatus.SUSPENDED, AccountStatus.CANCELLED])
async def test_an_unmonitored_account_is_told_so_and_not_troubleshot(status):
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        tool("look_up_steps", problem="my door sensor is offline"),
        Reply(text="Your monitoring isn't active. Let me get you to that team."),
    ])
    convo = build(model, salesforce=FakeSalesforce(customer=a_customer(status)))
    await convo.say("415 555 0142, code 8241, my door sensor is offline")
    verify = convo.record.tool_calls[1]["result"]
    assert verify["monitored"] is False
    assert "not being monitored" in verify["guidance"]
    steps = next(c for c in convo.record.tool_calls if c["name"] == "look_up_steps")
    assert steps["result"]["refused"] == policy.Denial.NOT_MONITORED.value


async def test_an_overdue_account_is_still_helped():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        tool("look_up_steps", problem="door sensor offline"),
        Reply(text="Let's start with the battery."),
    ])
    convo = build(model, salesforce=FakeSalesforce(customer=a_customer(AccountStatus.PAST_DUE)))
    await convo.say("415 555 0142, code 8241, sensor offline")
    steps = next(c for c in convo.record.tool_calls if c["name"] == "look_up_steps")
    assert steps["result"]["steps_found"] is True


# ---- grounding ----

async def test_no_steps_are_offered_when_nothing_is_retrieved():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        tool("look_up_steps", problem="my toaster is broken"),
        Reply(text="I don't have a fix for that. Let me get you a person."),
    ])
    convo = build(model, retriever=FakeRetriever(passages=[]))
    await convo.say("415 555 0142, code 8241, my toaster is broken")
    steps = next(c for c in convo.record.tool_calls if c["name"] == "look_up_steps")
    assert steps["result"]["steps_found"] is False
    assert "do not improvise" in steps["result"]["guidance"].lower()


async def test_retrieved_steps_reach_the_model_as_the_only_source():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        tool("look_up_steps", problem="door sensor offline"),
        Reply(text="Take the cover off the sensor."),
    ])
    convo = build(model)
    await convo.say("415 555 0142, code 8241, door sensor offline")
    final_system = model.calls[-1]["system"]
    assert "only instructions you may give" in final_system
    assert "Take the cover off the sensor." in final_system


# ---- repeat failures ----

async def test_equipment_that_keeps_failing_is_not_repaired_again():
    history = CaseHistory(cases=[
        SupportCase(f"0000{i}", "Sensor offline", "", "Closed", "DEV-2001", date(2026, i + 1, 1))
        for i in range(3)
    ])
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        tool("check_passcode", spoken="8241"),
        tool("list_equipment"),
        tool("look_up_steps", problem="door sensor offline", device_external_id="DEV-2001"),
        Reply(text="This has been out three times. It needs replacing."),
    ])
    convo = build(model, salesforce=FakeSalesforce(history=history))
    await convo.say("415 555 0142, code 8241, the front door sensor is offline again")
    steps = next(c for c in convo.record.tool_calls if c["name"] == "look_up_steps")
    assert steps["result"]["refused"] == policy.Denial.REPEAT_FAILURE.value
    assert "replacing" in steps["result"]["guidance"]


# ---- confirming a fix ----

async def test_a_fix_is_confirmed_by_the_equipment_not_by_the_caller():
    model = ScriptedModel([
        tool("recheck_equipment", device_external_id="DEV-2001"),
        Reply(text="It's reporting again. You're all set."),
    ])
    convo = build(model)
    convo.state.customer = a_customer()
    convo.state.verified = True
    await convo.say("okay I've put the battery back in")
    result = convo.record.tool_calls[0]["result"]
    assert result["reporting"] is True


async def test_equipment_that_will_not_recover_is_reported_honestly():
    model = ScriptedModel([
        tool("recheck_equipment", device_external_id="DEV-2001"),
        Reply(text="It's still not reporting. Let me open a case."),
    ])
    convo = build(model, telemetry=FakeTelemetry([a_device(recovers=False)]))
    convo.state.customer = a_customer()
    convo.state.verified = True
    await convo.say("done that")
    result = convo.record.tool_calls[0]["result"]
    assert result["reporting"] is False
    assert "honestly" in result["guidance"]


# ---- things that stop the call ----

async def test_an_emergency_ends_the_call_without_consulting_the_model():
    model = ScriptedModel([Reply(text="should never be reached")])
    convo = build(model)
    reply = await convo.say("there's a fire in my kitchen")
    assert "emergency services" in reply.lower()
    assert model.calls == []


async def test_asking_for_a_person_is_recorded_immediately():
    model = ScriptedModel([tool("hand_to_a_person", reason="caller asked"),
                           Reply(text="Someone will call you back.")])
    convo = build(model)
    await convo.say("can I just speak to a real person please")
    assert convo.state.caller_requested_human


async def test_an_out_of_scope_question_is_flagged_to_the_model():
    model = ScriptedModel([Reply(text="That's not something I can help with.")])
    convo = build(model)
    await convo.say("why was I charged twice this month")
    assert policy.Denial.OUT_OF_SCOPE.value in convo.record.denials
    assert "not about broken equipment" in model.calls[0]["system"]


# ---- what gets said ----

async def test_account_details_are_withheld_if_the_model_tries_to_say_them_early():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        Reply(text="Hello Priya Raghunathan at 1 Fell Street, how can I help?"),
    ])
    convo = build(model)
    reply = await convo.say("415 555 0142")
    assert reply == WITHHELD
    assert policy.Denial.NOT_VERIFIED.value in convo.record.denials


async def test_a_commitment_the_agent_cannot_keep_is_replaced():
    model = ScriptedModel([Reply(text="A technician will be there within 2 hours.")])
    convo = build(model)
    reply = await convo.say("when can someone come out")
    assert "within 2 hours" not in reply
    assert "unkeepable_promise" in convo.record.denials


async def test_a_looping_turn_gives_up_rather_than_running_forever():
    model = ScriptedModel([tool("list_equipment") for _ in range(20)])
    convo = build(model)
    convo.state.customer = a_customer()
    convo.state.verified = True
    reply = await convo.say("what's wrong")
    assert "call you back" in reply.lower()


# ---- the record ----

async def test_the_call_is_written_down():
    model = ScriptedModel([
        tool("find_customer", phone="415 555 0142"),
        Reply(text="What's the passcode?"),
    ])
    convo = build(model)
    await convo.say("hi, 415 555 0142")
    assert [t.speaker for t in convo.record.turns] == ["caller", "agent"]
    assert convo.record.tool_calls[0]["name"] == "find_customer"
    assert convo.record.id.startswith("CALL-")
