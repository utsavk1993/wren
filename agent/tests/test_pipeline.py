"""Checks on how a spoken turn is carried."""

from __future__ import annotations

import asyncio

import pytest

from voice.pipeline import (
    ACKNOWLEDGE_AFTER_MS,
    HOLDING_LINES,
    CallSession,
    VoicePipeline,
)
from voice.speech import Transcript
from voice.turn_taking import SETTLED_SILENCE_MS, UNFINISHED_SILENCE_MS


class FakeRecord:
    def __init__(self):
        self.tool_calls: list[dict] = []


class SlowConversation:
    """Stands in for the conversation, taking a controllable amount of time.

    Whether a tool was reached for is part of the fixture, because that is what
    decides whether the caller is told to hold on.
    """

    def __init__(self, reply: str, delay_s: float = 0.0, uses_a_tool: bool = True):
        self.reply = reply
        self.delay_s = delay_s
        self.uses_a_tool = uses_a_tool
        self.heard: list[str] = []
        self.record = FakeRecord()

    async def say(self, utterance: str) -> str:
        self.heard.append(utterance)
        if self.uses_a_tool:
            self.record.tool_calls.append({"name": "find_customer"})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.reply


def build(
    reply="Take the cover off. Remove the battery.",
    delay_s=0.0,
    uses_a_tool=True,
) -> VoicePipeline:
    return VoicePipeline(
        CallSession(conversation=SlowConversation(reply, delay_s, uses_a_tool))
    )


# ---- deciding the caller has finished ----

async def test_a_finished_sentence_produces_a_turn():
    pipe = build()
    assert await pipe.heard(Transcript("my sensor is offline", is_final=True)) is None
    assert await pipe.heard(
        Transcript("", is_final=True), silence_ms=SETTLED_SILENCE_MS
    ) == "my sensor is offline"


async def test_a_caller_pausing_mid_sentence_is_not_cut_off():
    pipe = build()
    await pipe.heard(Transcript("I want to reset the", is_final=False))
    # Long enough to end a finished sentence, but this one is not finished.
    assert await pipe.heard(
        Transcript("", is_final=False), silence_ms=SETTLED_SILENCE_MS
    ) is None
    assert SETTLED_SILENCE_MS < UNFINISHED_SILENCE_MS, "the whole point of the two"
    await pipe.heard(Transcript("I want to reset the panel", is_final=True))
    assert await pipe.heard(
        Transcript("", is_final=True), silence_ms=SETTLED_SILENCE_MS
    ) == "I want to reset the panel"


# ---- filling the wait ----

async def test_a_slow_lookup_is_acknowledged_before_the_answer():
    pipe = build(delay_s=(ACKNOWLEDGE_AFTER_MS / 1000) + 0.25)
    spoken = [u async for u in pipe.respond_to("my sensor is offline")]
    assert spoken[0].is_acknowledgement
    assert spoken[0].text in HOLDING_LINES
    assert [u.text for u in spoken[1:]] == \
        ["Take the cover off.", "Remove the battery."]


async def test_a_slow_turn_with_no_lookup_is_not_narrated():
    """Waiting on the model is not worth mentioning; waiting on another system is.

    Saying something before every slow reply is what made the agent sound
    mechanical, because on this system every reply is slow.
    """
    pipe = build(delay_s=(ACKNOWLEDGE_AFTER_MS / 1000) + 0.25, uses_a_tool=False)
    spoken = [u async for u in pipe.respond_to("my sensor is offline")]
    assert not any(u.is_acknowledgement for u in spoken)


async def test_the_same_holding_line_is_never_heard_twice():
    pipe = build(delay_s=(ACKNOWLEDGE_AFTER_MS / 1000) + 0.25)
    heard = []
    for _ in range(3):
        async for utterance in pipe.respond_to("still not working"):
            if utterance.is_acknowledgement:
                heard.append(utterance.text)
    assert len(heard) == len(set(heard)), f"repeated itself: {heard}"


async def test_a_quick_turn_gets_no_pointless_preamble():
    pipe = build(delay_s=0.0)
    spoken = [u async for u in pipe.respond_to("my sensor is offline")]
    assert not any(u.is_acknowledgement for u in spoken)


async def test_the_reply_is_released_a_sentence_at_a_time():
    pipe = build(reply="One thing. Then another. Then a third.")
    spoken = [u.text async for u in pipe.respond_to("what do I do")]
    assert spoken == ["One thing.", "Then another.", "Then a third."]


# ---- interrupting ----

async def test_the_rest_of_a_reply_is_abandoned_when_the_caller_cuts_in():
    pipe = build(reply="First step. Second step. Third step.")
    session = pipe.session
    produced = []
    async for utterance in pipe.respond_to("what do I do"):
        produced.append(utterance.text)
        # The caller starts talking after hearing the first sentence.
        session.interrupted = True
    assert produced == ["First step."]


async def test_speaking_over_the_agent_marks_the_call_interrupted():
    pipe = build()
    pipe.session.barge_in.agent_started_speaking()
    await pipe.heard(Transcript("no wait", is_final=False))
    assert pipe.session.interrupted


async def test_talking_while_the_agent_is_quiet_is_not_an_interruption():
    pipe = build()
    await pipe.heard(Transcript("hello", is_final=False))
    assert not pipe.session.interrupted


async def test_playback_does_nothing_without_speech_credentials():
    pipe = build()
    sent: list[bytes] = []
    from voice.pipeline import Utterance
    await pipe.play(Utterance("anything"), sent.append)
    assert sent == []
