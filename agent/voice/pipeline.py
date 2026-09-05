"""One call, carried over audio.

Audio arrives, becomes words, the conversation decides what to say, and the
reply becomes audio again. Every stage overlaps the next: partial transcripts
arrive while the caller is still speaking, and speech synthesis starts on the
first finished sentence rather than the finished reply.

The caller is told something is happening before the answer is ready. A turn
that needs two or three lookups takes seconds, and silence for that long reads
as a dropped call, so a short acknowledgement goes out first and the real answer
follows it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from conversation import Conversation
from observability import StageTimer
from voice.speech import SentenceBuffer, Speaker, Transcript
from voice.turn_taking import BargeInDetector, TurnDetector

log = logging.getLogger(__name__)

GREETING = (
    "Thanks for calling, my name is Wren. Can I take the phone number on your account?"
)

# Said while lookups are running, so the caller hears something during a wait
# that would otherwise be silence.
WORKING_ON_IT = "Let me take a look."

# Long enough that a quick answer does not get a pointless preamble.
ACKNOWLEDGE_AFTER_MS = 700


@dataclass
class Utterance:
    """Something for the caller to hear."""

    text: str
    is_acknowledgement: bool = False


@dataclass
class CallSession:
    conversation: Conversation
    speaker: Speaker | None = None
    turns: TurnDetector = field(default_factory=TurnDetector)
    barge_in: BargeInDetector = field(default_factory=BargeInDetector)
    interrupted: bool = False

    def caller_spoke_over_us(self) -> bool:
        return self.interrupted


class VoicePipeline:
    """Drives one call from audio in to audio out."""

    def __init__(self, session: CallSession) -> None:
        self.session = session
        self._speaking: asyncio.Task | None = None

    # ---- what the caller says ----

    async def heard(self, transcript: Transcript, silence_ms: float = 0.0) -> str | None:
        """Feed in a transcript; get back a reply when the turn has ended."""
        session = self.session

        if transcript.text:
            session.turns.heard_speech(transcript.text)
            if session.barge_in.agent_is_speaking:
                session.interrupted = True

        # A transcript marked final by the transcription service only means it
        # will not revise those words. It says nothing about whether the caller
        # has finished talking, and treating it as the end of a turn is what
        # cuts people off mid-sentence. Only the silence judgement ends a turn.
        if not silence_ms:
            return None
        if not session.turns.heard_silence(silence_ms).finished:
            return None

        said = session.turns.transcript
        session.turns.reset()
        if not said.strip():
            return None
        return said

    # ---- what the agent says ----

    async def respond_to(self, said: str) -> AsyncIterator[Utterance]:
        """Produce what the caller hears, acknowledging first if it will be slow."""
        session = self.session
        session.interrupted = False

        reply_task = asyncio.create_task(session.conversation.say(said))
        done, _ = await asyncio.wait({reply_task}, timeout=ACKNOWLEDGE_AFTER_MS / 1000)
        if not done:
            # Still working. Say so rather than leaving the line silent.
            yield Utterance(WORKING_ON_IT, is_acknowledgement=True)

        reply = await reply_task
        buffer = SentenceBuffer()
        for sentence in buffer.add(reply) + buffer.flush():
            if session.caller_spoke_over_us():
                log.info("abandoning the rest of the reply: caller interrupted")
                return
            yield Utterance(sentence)

    async def play(
        self,
        utterance: Utterance,
        send_audio: Callable[[bytes], object],
    ) -> None:
        """Speak one sentence, stopping the moment the caller cuts in."""
        session = self.session
        if session.speaker is None or not session.speaker.configured:
            return
        session.barge_in.agent_started_speaking()
        try:
            async for chunk in session.speaker.speak(
                utterance.text, should_stop=session.caller_spoke_over_us
            ):
                result = send_audio(chunk)
                if asyncio.iscoroutine(result):
                    await result
        finally:
            session.barge_in.agent_stopped_speaking()

    def stop_speaking(self) -> None:
        session = self.session
        session.interrupted = True
        session.barge_in.agent_stopped_speaking()


def new_timer() -> StageTimer:
    return StageTimer()
