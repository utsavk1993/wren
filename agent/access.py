"""Keeping a public link from becoming an open tab on someone else's budget.

Every call spends tokens, transcription minutes, and requests against a customer
system allowed fifteen thousand a day. A link anyone can open is a link anyone
can hold open, and a few thousand calls would lock that account out for
everyone, including whoever the demo was for.

Two limits, because they fail differently. A passphrase decides who may start a
call at all. The caps decide how much can be spent even by people who are
supposed to be here.

Both are off unless configured, so running it locally is unchanged.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Enough for a few people trying it at once, few enough that nobody can hold
# every line open.
DEFAULT_MAX_CONCURRENT = 3

# A day's worth, sized so it cannot exhaust the customer system's own daily
# allowance. Each call makes a handful of requests against it.
DEFAULT_MAX_PER_DAY = 200

A_DAY = 24 * 60 * 60


@dataclass
class Gate:
    """Who may call, and how often."""

    passphrase: str = field(default_factory=lambda: os.getenv("WREN_PASSPHRASE", ""))
    max_concurrent: int = field(
        default_factory=lambda: int(os.getenv("WREN_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT))
    )
    max_per_day: int = field(
        default_factory=lambda: int(os.getenv("WREN_MAX_CALLS_PER_DAY", DEFAULT_MAX_PER_DAY))
    )
    _in_progress: int = 0
    _started: deque[float] = field(default_factory=deque)

    @property
    def guarded(self) -> bool:
        return bool(self.passphrase)

    def check_passphrase(self, given: str) -> bool:
        """Compare without leaking how much of it was right.

        A plain equality check returns as soon as two characters differ, and the
        time that takes can be measured to work the value out one character at a
        time. This takes the same time whatever is passed.
        """
        if not self.passphrase:
            return True
        return hmac.compare_digest(self.passphrase, (given or "").strip())

    def _forget_old(self) -> None:
        cutoff = time.time() - A_DAY
        while self._started and self._started[0] < cutoff:
            self._started.popleft()

    def may_start(self) -> tuple[bool, str]:
        """Whether another call can begin, and what to say if not."""
        self._forget_old()
        if self._in_progress >= self.max_concurrent:
            return False, (
                "There are already as many calls going as this can handle. "
                "Try again in a minute."
            )
        if len(self._started) >= self.max_per_day:
            return False, (
                "This has taken as many calls as it can today. "
                "It will accept more tomorrow."
            )
        return True, ""

    def started(self) -> None:
        self._in_progress += 1
        self._started.append(time.time())

    def ended(self) -> None:
        # Never below zero: a call that fails before it starts would otherwise
        # leave a permanent gap in the count.
        self._in_progress = max(0, self._in_progress - 1)

    def status(self) -> dict[str, object]:
        self._forget_old()
        return {
            "guarded": self.guarded,
            "in_progress": self._in_progress,
            "max_concurrent": self.max_concurrent,
            "today": len(self._started),
            "max_per_day": self.max_per_day,
        }


_gate: Gate | None = None


def gate() -> Gate:
    global _gate
    if _gate is None:
        _gate = Gate()
        if _gate.guarded:
            log.info("a passphrase is required to start a call")
        log.info(
            "at most %d calls at once, %d a day", _gate.max_concurrent, _gate.max_per_day
        )
    return _gate
