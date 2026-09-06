"""Deciding when the caller has finished, and stopping when they have not.

This is the hardest part of talking to someone over audio and where most of the
delay hides. Silence alone is a poor signal: people pause mid-sentence to think,
and cutting in on "I want to reset the... the panel" is worse than waiting a
moment too long.

Two separate judgements are made here. Whether the caller has finished a turn,
and whether the caller has started talking over the agent, which has to stop it
speaking immediately.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Silence after a sentence that sounds complete.
#
# Quiet is measured by how long it has been since the transcription service
# recognised anything, and that service reports in batches rather than
# continuously. A gap of several hundred milliseconds appears mid-sentence while
# someone is still speaking, so a threshold tuned to real silence cuts callers
# off halfway through. This is set above that batching gap, which costs a little
# dead air and is worth it.
SETTLED_SILENCE_MS = 900

# Silence after something that sounds unfinished. Longer, because the caller is
# almost certainly still assembling the sentence.
UNFINISHED_SILENCE_MS = 1600

# However unfinished it sounds, this much quiet means the turn is over. Without
# it, a caller who trails off is never answered.
MAX_SILENCE_MS = 2600

# Enough speech over the agent to count as interrupting rather than a cough or a
# door closing.
BARGE_IN_MS = 220

# Words that almost always have something after them. A pause here is someone
# thinking, not finishing.
TRAILING_WORDS = {
    "and", "but", "so", "or", "the", "a", "an", "my", "it's", "its", "is", "was",
    "to", "of", "for", "with", "that", "this", "then", "when", "if", "because",
    "um", "uh", "er", "like", "well", "i", "we", "you", "he", "she", "they",
}

# Filler that means the caller is still working out what to say.
HESITATION = re.compile(r"\b(um+|uh+|er+|hmm+|erm+)\b\s*$", re.I)


@dataclass
class TurnDecision:
    finished: bool
    wait_ms: int
    reason: str


def looks_finished(transcript: str) -> bool:
    """Whether what has been said so far sounds like a complete thought."""
    text = (transcript or "").strip()
    if not text:
        return False
    if HESITATION.search(text):
        return False
    if text.endswith(("...", "-", ",")):
        return False
    if text.endswith((".", "?", "!")):
        return True
    last = re.sub(r"[^\w']", "", text.split()[-1]).lower()
    return last not in TRAILING_WORDS


def silence_needed(transcript: str) -> TurnDecision:
    """How long to wait before treating the caller as finished."""
    if looks_finished(transcript):
        return TurnDecision(False, SETTLED_SILENCE_MS, "sounds complete")
    return TurnDecision(False, UNFINISHED_SILENCE_MS, "sounds unfinished")


@dataclass
class TurnDetector:
    """Watches speech and silence and says when a turn has ended."""

    transcript: str = ""
    silence_ms: float = 0.0
    _speaking_since: float | None = field(default=None, repr=False)

    def heard_speech(self, transcript: str) -> None:
        self.transcript = transcript
        self.silence_ms = 0.0

    def heard_silence(self, milliseconds: float) -> TurnDecision:
        self.silence_ms += milliseconds
        if not self.transcript.strip():
            return TurnDecision(False, SETTLED_SILENCE_MS, "nothing said yet")

        needed = silence_needed(self.transcript)
        if self.silence_ms >= MAX_SILENCE_MS:
            return TurnDecision(True, 0, "quiet long enough regardless")
        if self.silence_ms >= needed.wait_ms:
            return TurnDecision(True, 0, needed.reason)
        return TurnDecision(False, int(needed.wait_ms - self.silence_ms), needed.reason)

    def reset(self) -> None:
        self.transcript = ""
        self.silence_ms = 0.0
        self._speaking_since = None


@dataclass
class BargeInDetector:
    """Notices the caller talking while the agent is still speaking.

    A short noise is not an interruption. Sustained speech is, and the agent has
    to stop mid-word: carrying on talking over someone is the single rudest
    thing a voice agent can do.
    """

    agent_is_speaking: bool = False
    _speech_started_at: float | None = field(default=None, repr=False)

    def agent_started_speaking(self) -> None:
        self.agent_is_speaking = True
        self._speech_started_at = None

    def agent_stopped_speaking(self) -> None:
        self.agent_is_speaking = False
        self._speech_started_at = None

    def caller_audio(self, is_speech: bool, now: float | None = None) -> bool:
        """Report whether the agent should stop talking."""
        if not self.agent_is_speaking:
            return False
        now = now if now is not None else time.monotonic()
        if not is_speech:
            self._speech_started_at = None
            return False
        if self._speech_started_at is None:
            self._speech_started_at = now
            return False
        if (now - self._speech_started_at) * 1000 >= BARGE_IN_MS:
            log.info("caller interrupted; stopping playback")
            return True
        return False
