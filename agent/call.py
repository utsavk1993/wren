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
import uuid
from typing import Any

from conversation import Conversation
from llm import get_model
from rag.retrieve import Retriever
from systems.salesforce import SalesforceClient
from systems.telemetry import TelemetryClient
from tools.dispatch import Dispatcher
from voice.pipeline import GREETING, CallSession, Utterance, VoicePipeline
from voice.speech import Speaker, Transcriber, Transcript
from voice.transport import audio_profile, configured_kind

log = logging.getLogger(__name__)


def capabilities() -> dict[str, Any]:
    profile = audio_profile()
    return {
        "transport": configured_kind().value,
        "sample_rate_hz": profile.sample_rate_hz,
        "speech_in": bool(os.environ.get("DEEPGRAM_API_KEY")),
        "speech_out": bool(
            os.environ.get("CARTESIA_API_KEY") and os.environ.get("CARTESIA_VOICE_ID")
        ),
        "conversation": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


class CallHandler:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self.call_id = f"CALL-{uuid.uuid4().hex[:12]}"
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

    async def aclose(self) -> None:
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

    async def run(self) -> None:
        await self._send("ready", call_id=self.call_id, **capabilities())
        await self._say(Utterance(GREETING))

        while True:
            raw = await self.websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                continue

            kind = message.get("type")
            if kind == "text":
                # Typed straight in, so no transcription and no turn detection.
                said = str(message.get("text", "")).strip()
                if said:
                    await self._handle(said)
            elif kind == "transcript":
                said = await self.pipeline.heard(
                    Transcript(
                        text=str(message.get("text", "")),
                        is_final=bool(message.get("is_final")),
                    ),
                    silence_ms=float(message.get("silence_ms", 0)),
                )
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
