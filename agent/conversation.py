"""Running one call.

Holds the transcript, decides when to reach for a tool, applies the rules
before and after the model speaks, and keeps the record of what happened.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import policy
from llm import LanguageModel, Reply
from observability import StageTimer, log_turn, tracer
from prompts import grounding_block, system_prompt
from tools.definitions import TOOLS
from tools.dispatch import Dispatcher

log = logging.getLogger(__name__)

# A turn that has gone round this many times is looping rather than working.
MAX_TOOL_ROUNDS = 6

# Which budget a tool call is charged against. Looking something up in the
# knowledge base is a different cost from calling out to another company's API,
# and they are tuned separately.
_TOOL_STAGES = {"look_up_steps": "retrieval"}


def _stage_for(tool_name: str) -> str:
    return _TOOL_STAGES.get(tool_name, "systems")


WITHHELD = (
    "I can't go through the account on this call. I can arrange for someone to "
    "call you back on the number we have on file."
)


@dataclass
class Turn:
    speaker: str
    text: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class CallRecord:
    id: str
    turns: list[Turn] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    denials: list[str] = field(default_factory=list)
    timings: list[dict[str, Any]] = field(default_factory=list)
    outcome: str | None = None


class Conversation:
    def __init__(self, model: LanguageModel, dispatcher: Dispatcher) -> None:
        self.model = model
        self.dispatcher = dispatcher
        self.state = policy.CallState()
        self.record = CallRecord(id=f"CALL-{uuid.uuid4().hex[:12]}")
        self._messages: list[dict[str, Any]] = []

    async def say(self, utterance: str) -> str:
        """Take what the caller said and produce what the agent says back."""
        self.record.turns.append(Turn("caller", utterance))
        timer = StageTimer()
        self._timer = timer

        # Read the caller before the model does. Some things end the call
        # regardless of what anyone was in the middle of.
        if policy.detect_emergency(utterance):
            self.state.emergency_declared = True
            return self._respond(
                "Please hang up and call the emergency services now. They can help "
                "you faster than I can."
            )
        if policy.detect_request_for_a_person(utterance):
            self.state.caller_requested_human = True
        scope = policy.check_scope(utterance)
        if not scope:
            self.record.denials.append(scope.reason.value)

        self._messages.append({"role": "user", "content": utterance})

        for _ in range(MAX_TOOL_ROUNDS):
            reply = await self.model.respond(
                system=self._system_text(scope),
                messages=self._messages,
                tools=TOOLS,
                timer=timer,
            )
            if not reply.wants_a_tool:
                return self._respond(self._vet(reply.text))

            self._messages.append(
                {"role": "assistant", "content": reply.raw_content or reply.text}
            )
            results = []
            for call in reply.tool_calls:
                with timer.measure(_stage_for(call.name)):
                    outcome = await self.dispatcher.run(call, self.state)
                self.record.tool_calls.append({"name": call.name, "result": outcome})
                if "refused" in outcome:
                    self.record.denials.append(outcome["refused"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": str(outcome),
                })
            # Every result goes back in one message. Splitting them teaches the
            # model to stop asking for more than one thing at a time.
            self._messages.append({"role": "user", "content": results})

        log.warning("call %s stopped making progress", self.record.id)
        return self._respond(
            "I'm having trouble getting to the bottom of this. Let me get someone "
            "to call you back."
        )

    def _system_text(self, scope: policy.Ruling) -> str:
        parts = [system_prompt()]
        if not scope:
            parts.append(f"THIS TURN: {scope.guidance}")
        parts.append(
            grounding_block(self.dispatcher.last_passages, self.dispatcher.last_titles)
        )
        return "\n\n".join(parts)

    def _vet(self, text: str) -> str:
        """Check what is about to be said before it is said."""
        if self.state.customer and not self.state.verified:
            # Nothing has been verified, so nothing about the account may be
            # confirmed, whatever the model has decided to write.
            for detail in (
                self.state.customer.full_name,
                self.state.customer.street,
                self.state.customer.plan,
            ):
                if detail and detail.lower() in text.lower():
                    log.warning("call %s: withheld unverified account detail", self.record.id)
                    self.record.denials.append(policy.Denial.NOT_VERIFIED.value)
                    return WITHHELD

        promises = policy.find_unkeepable_promises(text)
        if promises:
            log.warning("call %s: stripped a commitment: %s", self.record.id, promises)
            self.record.denials.append("unkeepable_promise")
            return (
                "I've made a note of this and someone from the team will call you "
                "back on the number on your account."
            )
        return text

    def _respond(self, text: str) -> str:
        timer = getattr(self, "_timer", None)
        timings: dict[str, Any] = {}
        if timer is not None:
            timings = timer.report(self.record.id).as_log_fields()
            self.record.timings.append(timings)

        said = next(
            (t.text for t in reversed(self.record.turns) if t.speaker == "caller"), ""
        )
        customer = self.state.customer
        log_turn(
            log,
            self.record.id,
            said=said,
            replied=text,
            # The identifier this project assigns, not the caller's name or
            # number: enough to find the account, not enough to be a leak.
            customer=customer.external_id if customer else None,
            verified=self.state.verified,
            account_status=customer.status.value if customer else None,
            tools=[c["name"] for c in self.record.tool_calls],
            refusals=self.record.denials,
            steps_given=len(self.state.steps_given),
            **timings,
        )
        tracer().turn(self.record.id, said, text, **timings)
        self.record.turns.append(Turn("agent", text))
        self._messages.append({"role": "assistant", "content": text})
        return text
