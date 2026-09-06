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

LISTEN_URL = "wss://api.deepgram.com/v1/listen"
SPEAK_URL = "https://api.deepgram.com/v1/speak"

# Both halves come from the same service. The alternative was one company for
# hearing and another for speaking, which meant two accounts, two bills and two
# places to look when something went wrong, for a difference of fifty
# milliseconds in the one stage that has never been the slow part.
DEFAULT_VOICE = "aura-2-thalia-en"

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
    # The service heard actual silence after this phrase. It has the audio and
    # can tell speech from quiet; this side only sees the gaps between
    # messages, which say nothing about whether anyone is still talking.
    paused: bool = False


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

    The work is split rather than handed over wholesale. The service reports
    when the caller has stopped making noise, which needs the audio and so can
    only be done there. Whether a stopped caller has finished a thought is
    decided here, because "I want to reset the..." is a pause and "my sensor is
    offline" is an ending, and telling those apart needs the words.
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
            "smart_format": "true",
            # Closes a phrase after a short pause so the words are finalised
            # promptly. This does not end a turn: people pause inside sentences
            # constantly, and treating every one as the end cuts them off after
            # three words.
            "endpointing": "300",
            # This is the signal that ends a turn. A full second of actual
            # silence, measured from the audio, which is something only the
            # service can do.
            "utterance_end_ms": "1000",
        }
        return LISTEN_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    @staticmethod
    def read_event(raw: str | bytes) -> Transcript | None:
        """Pull a transcript out of one message from the provider."""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        # A quiet stretch after the caller stopped, carrying no words.
        if payload.get("type") == "UtteranceEnd":
            return Transcript(text="", is_final=True, paused=True)

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
        voice: str | None = None,
        sample_rate_hz: int = 16000,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        self.voice = voice or os.environ.get("DEEPGRAM_VOICE", DEFAULT_VOICE)
        self.sample_rate_hz = sample_rate_hz
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._owns_http = http is None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _params(self) -> dict[str, str]:
        return {
            "model": self.voice,
            # Raw signed 16-bit audio, matching what the browser plays and what
            # the transcription side is given, so nothing has to be converted
            # in between.
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate_hz),
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
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        async with self._http.stream(
            "POST", SPEAK_URL, headers=headers,
            params=self._params(), json={"text": text},
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
