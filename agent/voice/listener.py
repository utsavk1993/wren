"""Listening to a caller for the length of a call.

Audio arrives from the browser and is passed straight through to the
transcription service, which sends words back as they are recognised. The
credentials stay here rather than in the browser, which is the reason the audio
takes this route at all instead of going direct.

Deciding that the caller has finished is not left to the transcription service.
It reports what it heard and how confident it is; whether a thought is complete
is judged here, where the rules about mid-sentence pauses live.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import websockets

from voice.speech import Transcriber, Transcript
from voice.turn_taking import MAX_SILENCE_MS, SETTLED_SILENCE_MS, worth_answering

log = logging.getLogger(__name__)

# How often to check whether the caller has gone quiet. Fine enough to notice a
# turn ending promptly, coarse enough not to spin.
SILENCE_TICK_MS = 100

# A safety net, not a turn detector. The service reports a pause after a second
# of real silence and does it well; this exists only for the case where that
# signal never arrives at all, so it sits far beyond it. Set anywhere near the
# service's own threshold and it fires first, cutting callers off mid-word.
ABANDONED_AFTER_MS = 8_000

# Nothing at all for this long means the line is idle rather than the caller
# thinking, so nothing more is expected of them.
IDLE_AFTER_MS = 15_000


class Listener:
    """Holds the connection to the transcription service for one call."""

    def __init__(
        self,
        transcriber: Transcriber,
        on_transcript: Callable[[Transcript, float], Any],
    ) -> None:
        self.transcriber = transcriber
        self._on_transcript = on_transcript
        self._socket: websockets.ClientConnection | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_audio_at = 0.0
        self._last_speech_at = 0.0
        self._speaking = False
        # What was heard and discarded, so a caller who is not getting through
        # leaves a trail rather than silence.
        self._ignored: list[dict[str, str]] = []

    @property
    def listening(self) -> bool:
        return self._socket is not None

    @property
    def ignored(self) -> list[dict[str, str]]:
        return list(self._ignored)

    async def start(self) -> None:
        if not self.transcriber.configured:
            log.info("no transcription credentials, so audio will be ignored")
            return
        self._socket = await websockets.connect(
            self.transcriber.connection_url(),
            additional_headers={"Authorization": f"Token {self.transcriber.api_key}"},
        )
        self._last_audio_at = time.monotonic()
        self._tasks = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self._watch_for_silence()),
        ]
        log.info("listening")

    async def send(self, audio: bytes) -> None:
        """Pass a piece of the caller's audio through."""
        if self._socket is None:
            return
        self._last_audio_at = time.monotonic()
        try:
            await self._socket.send(audio)
        except websockets.ConnectionClosed:
            log.warning("transcription connection closed while sending")
            self._socket = None

    async def _read(self) -> None:
        """Hand each recognised phrase on as it arrives."""
        assert self._socket is not None
        try:
            async for raw in self._socket:
                result = self.transcriber.read_event(raw)
                if result is None:
                    continue

                # A pause carries no words and is always passed on; it is the
                # signal that ends a turn.
                if result.text:
                    keep, why = worth_answering(result.text, result.confidence)
                    if not keep:
                        log.info("ignored %r: %s", result.text[:40], why)
                        self._ignored.append({"text": result.text, "why": why})
                        continue

                self._last_speech_at = time.monotonic()
                # A pause reported by the service is real silence in the audio.
                # Passing it on as elapsed quiet lets the rules about whether a
                # thought sounds finished decide whether the turn is over, or
                # whether the caller is still assembling a sentence.
                silence = SETTLED_SILENCE_MS if result.paused else 0.0
                await self._deliver(result, silence)
        except websockets.ConnectionClosed:
            log.info("transcription connection closed")
        except Exception:
            log.exception("transcription failed")

    async def _watch_for_silence(self) -> None:
        """Answer a caller who trails off and never comes back.

        The service reports a pause when speech stops, which covers the ordinary
        case. It does not always report one when someone stops mid-word and says
        nothing further, and that caller still deserves an answer.
        """
        try:
            while True:
                await asyncio.sleep(SILENCE_TICK_MS / 1000)
                quiet_ms = (time.monotonic() - self._last_speech_at) * 1000
                if not (ABANDONED_AFTER_MS < quiet_ms < IDLE_AFTER_MS):
                    continue
                self._last_speech_at = time.monotonic()
                await self._deliver(Transcript(text="", is_final=True, paused=True),
                                    MAX_SILENCE_MS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("silence watch failed")

    async def _deliver(self, transcript: Transcript, silence_ms: float) -> None:
        result = self._on_transcript(transcript, silence_ms)
        if asyncio.iscoroutine(result):
            await result

    def turn_taken(self) -> None:
        """Called once a turn has been handed on, so the clock starts again."""
        self._speaking = False

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._socket is not None:
            try:
                await self._socket.send(json.dumps({"type": "CloseStream"}))
                await self._socket.close()
            except Exception:  # noqa: BLE001 - the call is ending regardless
                pass
            self._socket = None
