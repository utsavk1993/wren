"""Handling one connected caller.

The browser sends either audio or typed text and receives the transcript, the
agent's words, and audio when speech is configured. Typed text is not a fallback
bolted on: it is how the conversation was built and tested, and it stays
available so a deployment without speech credentials is still usable.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from conversation import Conversation
from llm import get_model
from rag.retrieve import Retriever
from systems.salesforce import SalesforceClient
from systems.telemetry import TelemetryClient
from tools.dispatch import Dispatcher
from voice.pipeline import GREETING, CallSession, Utterance, VoicePipeline
from voice.listener import Listener
from voice.speech import Speaker, Transcriber
from voice.transport import audio_profile, configured_kind

log = logging.getLogger(__name__)


def capabilities() -> dict[str, Any]:
    profile = audio_profile()
    return {
        "transport": configured_kind().value,
        "sample_rate_hz": profile.sample_rate_hz,
        "speech_in": bool(os.environ.get("DEEPGRAM_API_KEY")),
        # Both halves come from the same service, so one key decides both.
        "speech_out": bool(os.environ.get("DEEPGRAM_API_KEY")),
        "conversation": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


async def warm_up() -> None:
    """Do the slow first-time work before anyone is waiting on it."""
    salesforce = SalesforceClient()
    retriever = Retriever()
    try:
        await salesforce.warm()
        # Loads the embedding model, which otherwise happens inside the first
        # lookup of the first call.
        await retriever.search("warm up")
    except Exception:  # noqa: BLE001 - starting up must not fail on this
        log.warning("could not warm up fully", exc_info=True)
    finally:
        await salesforce.aclose()
        await retriever.aclose()


class CallHandler:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        profile = audio_profile()
        self.salesforce = SalesforceClient()
        self.telemetry = TelemetryClient()
        self.retriever = Retriever()
        self.transcriber = Transcriber(sample_rate_hz=profile.sample_rate_hz)
        self.speaker = Speaker(sample_rate_hz=profile.sample_rate_hz)
        conversation = Conversation(
            get_model(), Dispatcher(self.salesforce, self.telemetry, self.retriever)
        )
        self.pipeline = VoicePipeline(
            CallSession(conversation=conversation, speaker=self.speaker)
        )
        # Audio from the browser goes through here rather than the browser
        # talking to the transcription service itself, which would put the
        # credentials in a page anyone can read.
        self.listener = Listener(self.transcriber, self._heard)
        # One identifier for the call, the same one the record and the logs use.
        # Two would mean a log line and a stored call could not be matched up.
        self.call_id = conversation.record.id

    async def aclose(self) -> None:
        # However the call ended, including badly, the record is closed off.
        await self.listener.aclose()
        self.pipeline.session.conversation.ended()
        await self.salesforce.aclose()
        await self.telemetry.aclose()
        await self.retriever.aclose()
        await self.speaker.aclose()

    async def _send(self, kind: str, **fields: Any) -> None:
        await self.websocket.send_text(json.dumps({"type": kind, **fields}))

    async def _send_audio(self, chunk: bytes) -> None:
        await self.websocket.send_text(
            json.dumps({"type": "audio", "pcm16": base64.b64encode(chunk).decode()})
        )

    async def _heard(self, transcript, silence_ms: float) -> None:
        """A phrase, or a report of how long the caller has been quiet."""
        if transcript.text:
            # Shown as it is recognised, so the caller can see they are heard
            # before the agent has decided they have finished.
            await self._send("hearing", text=transcript.text)
        said = await self.pipeline.heard(transcript, silence_ms=silence_ms)
        if said:
            self.listener.turn_taken()
            await self._handle(said)

    async def run(self) -> None:
        await self.listener.start()
        await self._send("ready", call_id=self.call_id, **capabilities())
        await self._say(Utterance(GREETING))

        while True:
            raw = await self.websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                continue

            kind = message.get("type")
            if kind == "audio":
                # The caller speaking. Straight through to be transcribed.
                await self.listener.send(base64.b64decode(message.get("pcm16", "")))
            elif kind == "text":
                # Typed straight in, so no transcription and no turn detection.
                said = str(message.get("text", "")).strip()
                if said:
                    await self._handle(said)
            elif kind == "interrupt":
                # The client noticed the caller talking over playback and has
                # already stopped the sound at its end.
                self.pipeline.stop_speaking()
                await self._send("interrupted")
            elif kind == "hangup":
                break

    async def _handle(self, said: str) -> None:
        await self._send("caller_said", text=said)
        async for utterance in self.pipeline.respond_to(said):
            await self._say(utterance)
        record = self.pipeline.session.conversation.record
        if record.timings:
            await self._send("timing", **record.timings[-1])

    async def _say(self, utterance: Utterance) -> None:
        await self._send(
            "agent_said", text=utterance.text, acknowledgement=utterance.is_acknowledgement
        )
        if self.speaker.configured:
            await self.pipeline.play(utterance, self._send_audio)
