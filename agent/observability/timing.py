"""Where a turn spends its time.

A single number for how long a reply took says nothing about what to fix. Each
stage is timed separately, because acceptable timings at every stage still add
up to a call that feels broken, and the only way to know which one to attack is
to have measured them apart.

The budget below is what the call has to fit into, from the caller finishing a
sentence to the agent starting to speak.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

TURN_BUDGET_MS = 1200

# What each stage is allowed before the turn stops feeling like a conversation.
STAGE_BUDGETS_MS = {
    "endpointing": 400,      # deciding the caller has stopped talking
    "transcription": 300,    # finishing the transcript
    "retrieval": 150,        # finding the right troubleshooting steps
    "systems": 250,          # customer and equipment lookups, run together
    "first_token": 500,      # the model beginning its reply
    "first_audio": 150,      # speech synthesis producing sound
}


@dataclass
class TurnTiming:
    stages: dict[str, float] = field(default_factory=dict)
    # How many times the model had to be consulted before it had something to
    # say. Each round is a full round trip, and the caller waits through all of
    # them, so this is usually the reason a turn is slow rather than the model
    # being slow.
    llm_rounds: int = 0

    @property
    def total_ms(self) -> float:
        # Only the stages on the path from silence to speech count. Anything
        # running alongside them is already paid for by the stage it overlaps.
        return sum(self.stages.get(k, 0.0) for k in STAGE_BUDGETS_MS)

    @property
    def over_budget(self) -> list[str]:
        return [
            name for name, spent in self.stages.items()
            if spent > STAGE_BUDGETS_MS.get(name, float("inf"))
        ]

    def as_log_fields(self) -> dict[str, float | str]:
        fields: dict[str, float | str] = {
            f"ms_{name}": round(spent, 1) for name, spent in self.stages.items()
        }
        fields["ms_total"] = round(self.total_ms, 1)
        if self.llm_rounds:
            fields["llm_rounds"] = self.llm_rounds
            fields["ms_first_token_per_round"] = round(
                self.stages.get("first_token", 0.0) / self.llm_rounds, 1
            )
        if self.over_budget:
            fields["over_budget"] = ",".join(self.over_budget)
        return fields


class StageTimer:
    """Records how long each part of a turn took."""

    def __init__(self) -> None:
        self.timing = TurnTiming()

    @contextmanager
    def measure(self, stage_name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(stage_name, (time.perf_counter() - started) * 1000)

    def count_llm_round(self) -> None:
        self.timing.llm_rounds += 1

    def record(self, stage_name: str, milliseconds: float) -> None:
        # Stages can happen more than once in a turn, such as two lookups; what
        # matters is the total spent there.
        self.timing.stages[stage_name] = self.timing.stages.get(stage_name, 0.0) + milliseconds

    def mark_from(self, stage_name: str, started_at: float) -> None:
        self.record(stage_name, (time.perf_counter() - started_at) * 1000)

    def report(self, call_id: str) -> TurnTiming:
        fields = self.timing.as_log_fields()
        if self.timing.over_budget:
            log.warning("turn over budget call=%s %s", call_id, fields)
        else:
            log.info("turn call=%s %s", call_id, fields)
        return self.timing


@contextmanager
def stage(timer: StageTimer | None, name: str):
    """Time a block, or do nothing when no timer is in play."""
    if timer is None:
        yield
        return
    with timer.measure(name):
        yield
