"""Where the audio comes from and goes back to.

The browser is one source of a caller's voice; a phone line is another. What
happens between the audio arriving and the audio going back is the same either
way, so the difference is kept to this one seam. Moving to a phone number later
means adding an implementation here, not changing the conversation.

The two paths are not identical in one respect worth remembering: a phone line
carries narrowband audio at eight kilohertz where a browser sends sixteen or
more, and transcription is measurably worse on the narrower signal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TransportKind(str, Enum):
    BROWSER = "browser"
    TELEPHONE = "telephone"


@dataclass(frozen=True)
class AudioProfile:
    sample_rate_hz: int
    channels: int = 1

    @property
    def is_narrowband(self) -> bool:
        return self.sample_rate_hz <= 8000


BROWSER_AUDIO = AudioProfile(sample_rate_hz=16000)
TELEPHONE_AUDIO = AudioProfile(sample_rate_hz=8000)


class Transport(Protocol):
    kind: TransportKind
    audio: AudioProfile

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


def configured_kind() -> TransportKind:
    return TransportKind(os.getenv("WREN_TRANSPORT", TransportKind.BROWSER.value))


def audio_profile(kind: TransportKind | None = None) -> AudioProfile:
    kind = kind or configured_kind()
    return TELEPHONE_AUDIO if kind is TransportKind.TELEPHONE else BROWSER_AUDIO
