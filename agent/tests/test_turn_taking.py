"""Checks on when the agent decides a caller has stopped talking."""

from __future__ import annotations

import pytest

from voice.turn_taking import (
    BARGE_IN_MS,
    MAX_SILENCE_MS,
    SETTLED_SILENCE_MS,
    UNFINISHED_SILENCE_MS,
    BargeInDetector,
    TurnDetector,
    looks_finished,
    silence_needed,
)


@pytest.mark.parametrize("said", [
    "my front door sensor is offline",
    "it says low battery.",
    "can you help me?",
    "the code is eight two four one",
])
def test_a_complete_thought_is_recognised(said):
    assert looks_finished(said)


@pytest.mark.parametrize("said", [
    "I want to reset the",
    "my sensor is showing um",
    "it's the one in the",
    "I tried that and",
    "the panel says...",
    "well I was going to say uh",
])
def test_an_unfinished_thought_is_recognised(said):
    assert not looks_finished(said)


def test_an_unfinished_sentence_earns_a_longer_pause():
    finished = silence_needed("my sensor is offline")
    midway = silence_needed("I want to reset the")
    assert midway.wait_ms > finished.wait_ms
    assert finished.wait_ms == SETTLED_SILENCE_MS
    assert midway.wait_ms == UNFINISHED_SILENCE_MS


def test_a_complete_sentence_ends_the_turn_quickly():
    detector = TurnDetector()
    detector.heard_speech("my door sensor is offline")
    assert not detector.heard_silence(300).finished
    assert detector.heard_silence(250).finished


def test_a_caller_thinking_mid_sentence_is_not_cut_off():
    detector = TurnDetector()
    detector.heard_speech("I want to reset the")
    # Long enough to end a finished sentence, but this one is not finished.
    assert not detector.heard_silence(SETTLED_SILENCE_MS + 100).finished
    assert not detector.heard_silence(200).finished
    # They carry on, exactly as the pause suggested they would.
    detector.heard_speech("I want to reset the panel")
    assert detector.heard_silence(SETTLED_SILENCE_MS + 50).finished


def test_a_caller_who_trails_off_is_still_answered():
    detector = TurnDetector()
    detector.heard_speech("I was going to say um")
    assert detector.heard_silence(MAX_SILENCE_MS).finished


def test_silence_before_anyone_speaks_ends_nothing():
    detector = TurnDetector()
    assert not detector.heard_silence(5000).finished


def test_speech_resets_the_silence_count():
    detector = TurnDetector()
    detector.heard_speech("my sensor")
    detector.heard_silence(400)
    detector.heard_speech("my sensor is offline")
    assert detector.silence_ms == 0


# ---- interrupting ----

def test_sustained_speech_over_the_agent_stops_it():
    barge = BargeInDetector()
    barge.agent_started_speaking()
    assert not barge.caller_audio(True, now=0.0)
    assert barge.caller_audio(True, now=BARGE_IN_MS / 1000 + 0.01)


def test_a_brief_noise_does_not_stop_the_agent():
    barge = BargeInDetector()
    barge.agent_started_speaking()
    barge.caller_audio(True, now=0.0)
    assert not barge.caller_audio(True, now=0.05)
    # Quiet again, so the next noise starts counting afresh.
    assert not barge.caller_audio(False, now=0.06)
    assert not barge.caller_audio(True, now=0.07)


def test_the_caller_is_not_interrupting_when_the_agent_is_silent():
    barge = BargeInDetector()
    assert not barge.caller_audio(True, now=0.0)
    assert not barge.caller_audio(True, now=10.0)


def test_playback_ending_clears_the_interruption_state():
    barge = BargeInDetector()
    barge.agent_started_speaking()
    barge.caller_audio(True, now=0.0)
    barge.agent_stopped_speaking()
    assert not barge.agent_is_speaking
    assert not barge.caller_audio(True, now=1.0)
