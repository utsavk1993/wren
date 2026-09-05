"""Turning the caller's audio into words, and the reply back into sound.

Both run as streams. Transcription emits partial text while the caller is still
speaking, so turn detection has something to judge before they stop. Speech
synthesis is handed one sentence at a time rather than the finished reply, so
the caller hears the beginning of an answer while the rest is still being
written.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"
CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2024-06-10"

# Ends a sentence, but not when the full stop belongs to an abbreviation or a
# decimal. Splitting on those hands speech synthesis a fragment.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$")

# Used while tokens are still arriving. The end-of-string case is left out on
# purpose: a full stop at the end of what has arrived so far may still turn out
# to be mid-sentence once the next token lands.
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass
class Transcript:
    text: str
    is_final: bool
    confidence: float = 1.0


def split_into_sentences(text: str) -> list[str]:
    """Break a reply where speech synthesis can safely start."""
    parts = [p.strip() for p in SENTENCE_END.split(text or "") if p and p.strip()]
    return parts


class SentenceBuffer:
    """Collects streamed tokens and releases whole sentences.

    Speech synthesis begins on the first complete sentence rather than the
    finished reply. That is what removes most of the wait: the caller hears the
    opening while the model is still writing the rest.
    """

    def __init__(self) -> None:
        self._pending = ""

    def add(self, chunk: str) -> list[str]:
        self._pending += chunk
        released: list[str] = []
        # Cut at each boundary and keep the raw remainder, spacing included. A
        # remainder that has been tidied up loses the space before the next
        # token and words run together.
        while (boundary := SENTENCE_BOUNDARY.search(self._pending)) is not None:
            sentence = self._pending[: boundary.end()].strip()
            self._pending = self._pending[boundary.end():]
            if sentence:
                released.append(sentence)
        return released

    def flush(self) -> list[str]:
        remaining = self._pending.strip()
        self._pending = ""
        return [remaining] if remaining else []


class Transcriber:
    """Streaming speech to text.

    Endpointing is left to this side rather than delegated to the provider: the
    rules about when a caller has finished a thought belong with the
    conversation, not with whichever service is doing the transcription.
    """

    def __init__(self, api_key: str | None = None, sample_rate_hz: int = 16000) -> None:
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        self.sample_rate_hz = sample_rate_hz

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def connection_url(self) -> str:
        params = {
            "model": "nova-3",
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate_hz),
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            # Off on purpose. Ending a turn is decided here, using how finished
            # the sentence sounds, not by silence alone.
            "endpointing": "false",
            "smart_format": "true",
        }
        return DEEPGRAM_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    @staticmethod
    def read_event(raw: str | bytes) -> Transcript | None:
        """Pull a transcript out of one message from the provider."""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        alternatives = (payload.get("channel") or {}).get("alternatives") or []
        if not alternatives:
            return None
        best = alternatives[0]
        text = (best.get("transcript") or "").strip()
        if not text:
            return None
        return Transcript(
            text=text,
            is_final=bool(payload.get("is_final")),
            confidence=float(best.get("confidence", 1.0)),
        )


class Speaker:
    """Streaming text to speech."""

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        sample_rate_hz: int = 16000,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")
        self.voice_id = voice_id or os.environ.get("CARTESIA_VOICE_ID", "")
        self.sample_rate_hz = sample_rate_hz
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._owns_http = http is None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.voice_id)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _body(self, text: str) -> dict:
        return {
            "model_id": "sonic-2",
            "transcript": text,
            "voice": {"mode": "id", "id": self.voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate_hz,
            },
            "language": "en",
        }

    async def speak(
        self,
        text: str,
        should_stop: Callable[[], bool] | None = None,
    ) -> AsyncIterator[bytes]:
        """Produce audio for one sentence, stopping if the caller cuts in."""
        if not self.configured:
            raise RuntimeError("no speech synthesis credentials configured")
        headers = {
            "X-API-Key": self.api_key,
            "Cartesia-Version": CARTESIA_VERSION,
            "Content-Type": "application/json",
        }
        async with self._http.stream(
            "POST", CARTESIA_URL, headers=headers, json=self._body(text)
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise RuntimeError(
                    f"speech synthesis failed: HTTP {response.status_code} {body[:200]!r}"
                )
            async for chunk in response.aiter_bytes():
                # Checked between chunks rather than only between sentences, so
                # an interruption stops the agent mid-word instead of at the end
                # of whatever it was saying.
                if should_stop and should_stop():
                    log.info("stopping playback: caller interrupted")
                    return
                yield chunk
