"""The rules the conversation is held to, enforced in code.

The instructions given to the model state these same rules, but a model can be
argued out of an instruction and will occasionally decide that being helpful
matters more than a constraint. Everything here is checked around the model
rather than inside it: what it may be told, what it is allowed to do, and what
it is permitted to say.

Every gate fails closed. Where the answer is unclear, the restrictive outcome
wins, because the failures on this side of the line are recoverable and the
ones on the other side are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from systems.models import CaseHistory, Customer, Device

# Two tries is enough to survive a mis-hearing and few enough to be no use to
# someone working through possibilities.
MAX_VERIFICATION_ATTEMPTS = 2

# Equipment repaired this many times before, now broken again, is a hardware
# fault rather than something the caller can fix.
REPEAT_FAILURE_THRESHOLD = 2

# Beyond this many attempted repairs on one call, the caller is being kept on
# the line rather than helped.
MAX_STEPS_BEFORE_HANDOFF = 8


class Denial(str, Enum):
    """Why the conversation is not allowed to do what it was about to."""

    NOT_VERIFIED = "not_verified"
    VERIFICATION_FAILED = "verification_failed"
    NOT_MONITORED = "not_monitored"
    OUT_OF_SCOPE = "out_of_scope"
    NO_GROUNDING = "no_grounding"
    REPEAT_FAILURE = "repeat_failure"
    STEPS_EXHAUSTED = "steps_exhausted"
    CALLER_ASKED_FOR_A_PERSON = "caller_asked_for_a_person"
    EMERGENCY = "emergency"


@dataclass
class Ruling:
    allowed: bool
    reason: Denial | None = None
    guidance: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Ruling(allowed=True)


def _denied(reason: Denial, guidance: str) -> Ruling:
    return Ruling(allowed=False, reason=reason, guidance=guidance)


@dataclass
class CallState:
    """What has happened so far on this call."""

    customer: Customer | None = None
    verified: bool = False
    verification_attempts: int = 0
    history: CaseHistory | None = None
    devices: list[Device] = field(default_factory=list)
    device_under_discussion: Device | None = None
    steps_given: list[str] = field(default_factory=list)
    caller_requested_human: bool = False
    emergency_declared: bool = False
    case_number: str | None = None

    @property
    def verification_exhausted(self) -> bool:
        return not self.verified and self.verification_attempts >= MAX_VERIFICATION_ATTEMPTS


# Words that mean the call is no longer about a sensor. Deliberately broad:
# treating a troubleshooting question as billing wastes a transfer, while
# treating a billing question as troubleshooting means improvising about money.
OUT_OF_SCOPE_PATTERNS = [
    r"\bbill(ing|s)?\b", r"\bcharge[ds]?\b", r"\brefund", r"\binvoice", r"\bpayment",
    r"\bcancel", r"\bterminate", r"\bcontract", r"\brenew", r"\bupgrade my (plan|package)",
    r"\breactivat", r"\brestart (my|the) (service|account)", r"\bturn (my|the) service back",
    r"\bprice", r"\bcost me\b", r"\bhow much\b", r"\bdiscount", r"\bpromotion",
    r"\badd (my|her|his|their) \w+ to the account", r"\bchange my address",
    r"\bmov(e|ing) house\b", r"\brelocat", r"\bnew customer\b", r"\bsign up\b",
]

# Things that stop the call being a support call at all.
EMERGENCY_PATTERNS = [
    r"\bfire\b", r"\bsmoke in\b", r"\bburning\b",
    r"\bsomeone('s| is)? (in|inside) (my|the) house\b", r"\bbreak(ing)? in\b",
    r"\bintruder", r"\bburglar", r"\bhurt\b", r"\binjured\b", r"\bbleeding\b",
    r"\bcan'?t breathe\b", r"\bheart attack\b", r"\bambulance\b",
]

ASKED_FOR_A_PERSON_PATTERNS = [
    r"\b(real|actual|human|live) (person|agent|operator)\b",
    r"\bspeak to (someone|a person|a human|an agent|a manager)\b",
    r"\btalk to (someone|a person|a human|an agent|a manager)\b",
    r"\bput me through\b", r"\btransfer me\b", r"\bget me a\b.*\b(person|human|manager)\b",
]

# Commitments the agent is in no position to make.
PROMISE_PATTERNS = [
    r"\bwithin (the )?(next )?\d+ (minute|hour|day|week)", r"\bby (tomorrow|tonight|monday|friday)",
    r"\bi('| wi)ll (send|dispatch|schedule|book|arrange)\b",
    r"\bwe('| wi)ll (waive|refund|credit|replace it free|cover the cost)\b",
    r"\bfree of charge\b", r"\bno charge\b", r"\bat no cost\b",
    r"\bguarantee", r"\bpromise you\b", r"\bdefinitely be\b",
]


def _matches(patterns: list[str], text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in patterns)


def detect_emergency(utterance: str) -> bool:
    return _matches(EMERGENCY_PATTERNS, utterance)


def detect_out_of_scope(utterance: str) -> bool:
    return _matches(OUT_OF_SCOPE_PATTERNS, utterance)


def detect_request_for_a_person(utterance: str) -> bool:
    return _matches(ASKED_FOR_A_PERSON_PATTERNS, utterance)


def may_disclose_account_details(state: CallState) -> Ruling:
    """Whether anything about the account may be said out loud yet.

    Covers the name on the account, the address, the plan, what equipment is
    installed and what has gone wrong before. All of it is confirmation that
    someone holds an account here, which is worth something to a stranger.
    """
    if state.customer is None:
        return _denied(
            Denial.NOT_VERIFIED,
            "Nobody has been identified yet. Ask for the phone number on the account.",
        )
    if state.verification_exhausted:
        return _denied(
            Denial.VERIFICATION_FAILED,
            "Verification failed twice. Do not confirm anything about the account. "
            "Offer a callback to the number already on file.",
        )
    if not state.verified:
        return _denied(
            Denial.NOT_VERIFIED,
            "Not yet verified. Ask for the four digit passcode before saying "
            "anything about the account.",
        )
    return ALLOWED


def may_troubleshoot(state: CallState) -> Ruling:
    """Whether walking the caller through a repair is the right thing to do.

    Being able to fix something is not the same as it being worth fixing. A
    repair on unmonitored equipment produces a caller who believes they are
    protected, which is worse than telling them plainly that they are not.
    """
    disclosure = may_disclose_account_details(state)
    if not disclosure:
        return disclosure

    assert state.customer is not None
    if not state.customer.status.is_monitored:
        return _denied(
            Denial.NOT_MONITORED,
            f"This account is {state.customer.status.value.lower()} and the equipment "
            "is not being monitored. Say so plainly, do not repair anything, and offer "
            "the team who can restart the service.",
        )

    if state.emergency_declared:
        return _denied(
            Denial.EMERGENCY,
            "The caller has described an emergency. Tell them to hang up and call "
            "the emergency services. Nothing else matters.",
        )

    if state.caller_requested_human:
        return _denied(
            Denial.CALLER_ASKED_FOR_A_PERSON,
            "The caller asked for a person. Hand off without making them justify it.",
        )

    device = state.device_under_discussion
    if device and state.history and state.history.is_repeat_failure(
        device.external_id, REPEAT_FAILURE_THRESHOLD
    ):
        previous = len(state.history.for_device(device.external_id))
        return _denied(
            Denial.REPEAT_FAILURE,
            f"This equipment has been repaired {previous} times before and has failed "
            "again. Do not repeat the same fix. Say it needs replacing, open a case, "
            "and get them a person.",
        )

    if device and device.is_faulty and not device.recovers_on_reset and state.steps_given:
        return _denied(
            Denial.REPEAT_FAILURE,
            "This equipment will not come back from a reset. Stop and arrange a "
            "replacement rather than continuing.",
        )

    if len(state.steps_given) >= MAX_STEPS_BEFORE_HANDOFF:
        return _denied(
            Denial.STEPS_EXHAUSTED,
            "Enough has been tried on this call. Open a case and hand off.",
        )

    return ALLOWED


def may_give_these_steps(state: CallState, retrieved_passages: list[str]) -> Ruling:
    """Whether there is anything to say about fixing this.

    Instructions come only from what retrieval returned. With nothing retrieved
    the honest answer is that there is no fix to offer, and the failure this
    prevents is the model producing plausible-sounding steps for a device it
    knows nothing about.
    """
    allowed = may_troubleshoot(state)
    if not allowed:
        return allowed
    if not retrieved_passages:
        return _denied(
            Denial.NO_GROUNDING,
            "Nothing was found for this problem. Say there is no fix available and "
            "offer a person. Do not improvise steps.",
        )
    return ALLOWED


def check_scope(utterance: str) -> Ruling:
    if detect_out_of_scope(utterance):
        return _denied(
            Denial.OUT_OF_SCOPE,
            "This is not about broken equipment. Say plainly that it is not something "
            "you handle, offer to put them through, and do not attempt a partial answer.",
        )
    return ALLOWED


def find_unkeepable_promises(reply: str) -> list[str]:
    """Commitments in a drafted reply that the agent cannot honour.

    Timing, cost and dispatch are decided by people and systems this agent has
    no contact with. A caller who is told a technician comes tomorrow has been
    given something nobody agreed to.
    """
    lowered = (reply or "").lower()
    return [m.group(0) for p in PROMISE_PATTERNS for m in re.finditer(p, lowered)]


def find_leaked_passcode(reply: str, passcode: str | None) -> bool:
    """Whether a drafted reply contains the passcode.

    Last line of defence. The passcode is never given to the model, so this
    should not be able to fire; it exists because the cost of being wrong about
    that is a spoken credential, and the check is nearly free.
    """
    if not passcode or not reply:
        return False
    digits_in_reply = re.findall(r"\d", reply)
    if passcode in re.sub(r"\D", "", reply):
        return True
    # Read out one digit at a time, the way a person says a code aloud.
    return "".join(digits_in_reply).find(passcode) >= 0


def should_escalate(state: CallState) -> Ruling:
    """Whether the call has reached the point of needing a person."""
    if state.emergency_declared:
        return _denied(Denial.EMERGENCY, "Emergency. Direct them to the emergency services.")
    if state.caller_requested_human:
        return _denied(Denial.CALLER_ASKED_FOR_A_PERSON, "The caller asked for a person.")
    if state.verification_exhausted:
        return _denied(Denial.VERIFICATION_FAILED, "Verification failed. Offer a callback.")
    if state.customer and not state.customer.status.is_monitored:
        return _denied(Denial.NOT_MONITORED, "The account is not monitored.")
    if len(state.steps_given) >= MAX_STEPS_BEFORE_HANDOFF:
        return _denied(Denial.STEPS_EXHAUSTED, "Everything reasonable has been tried.")
    return ALLOWED
