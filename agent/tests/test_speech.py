"""Checks on splitting a reply for speech, and reading transcription events."""

from __future__ import annotations

import json

import pytest

from voice.speech import SentenceBuffer, Transcriber, split_into_sentences


@pytest.mark.parametrize("text,expected", [
    ("Take the cover off. Remove the battery.",
     ["Take the cover off.", "Remove the battery."]),
    ("Is it blinking?", ["Is it blinking?"]),
    ("Wait about thirty seconds. Then check the panel. Tell me what it says.",
     ["Wait about thirty seconds.", "Then check the panel.", "Tell me what it says."]),
])
def test_a_reply_is_split_where_speech_can_start(text, expected):
    assert split_into_sentences(text) == expected


def test_an_abbreviation_does_not_split_a_sentence():
    # Splitting here would hand speech synthesis half an instruction.
    text = "The battery is a CR123A. Put it back the same way round."
    assert len(split_into_sentences(text)) == 2


def test_speech_starts_before_the_reply_is_finished():
    buffer = SentenceBuffer()
    assert buffer.add("Take the cover off") == []
    # The moment the first sentence closes, there is something to say out loud
    # while the rest is still being written.
    assert buffer.add(". Remove the ") == ["Take the cover off."]
    assert buffer.add("battery.") == []
    assert buffer.flush() == ["Remove the battery."]


def test_nothing_is_left_behind_at_the_end():
    buffer = SentenceBuffer()
    buffer.add("One. Two")
    assert buffer.flush() == ["Two"]
    assert buffer.flush() == []


def test_endpointing_is_not_delegated_to_the_provider():
    # When a caller has finished a thought is decided here, from how complete
    # the sentence sounds, not by the transcription service watching for silence.
    url = Transcriber(api_key="x").connection_url()
    assert "endpointing=false" in url
    assert "interim_results=true" in url


def test_the_sample_rate_follows_the_transport():
    assert "sample_rate=8000" in Transcriber(api_key="x", sample_rate_hz=8000).connection_url()
    assert "sample_rate=16000" in Transcriber(api_key="x").connection_url()


def test_a_transcript_is_read_out_of_a_provider_event():
    event = json.dumps({
        "is_final": True,
        "channel": {"alternatives": [{"transcript": "my sensor is offline",
                                      "confidence": 0.97}]},
    })
    result = Transcriber.read_event(event)
    assert result.text == "my sensor is offline"
    assert result.is_final
    assert result.confidence == pytest.approx(0.97)


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps({"channel": {"alternatives": []}}),
    json.dumps({"channel": {"alternatives": [{"transcript": "   "}]}}),
    json.dumps({}),
])
def test_events_carrying_nothing_useful_are_ignored(raw):
    assert Transcriber.read_event(raw) is None
