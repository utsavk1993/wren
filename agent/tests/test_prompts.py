"""Checks on the agent's instructions.

The prompt is one layer of the guardrails and the weaker of the two: a model
can be talked out of an instruction. The checks here are that each rule is
present and stated, not that it will always be obeyed. Enforcement in code is
what makes them hold.
"""

from __future__ import annotations

import re

import pytest

from prompts import MAX_SYSTEM_PROMPT_WORDS, grounding_block, system_prompt


def test_prompt_stays_within_its_budget():
    # Raises rather than returns if it has grown too large.
    text = system_prompt()
    assert len(text.split()) <= MAX_SYSTEM_PROMPT_WORDS


@pytest.mark.parametrize(
    "rule,phrases",
    [
        ("identify the caller", ["phone number"]),
        ("verify before disclosing", ["passcode", "until this passes"]),
        ("never speak the passcode", ["never say it out loud", "never read it back"]),
        ("stop after two failures", ["second wrong answer", "call them back"]),
        ("check the account is active", ["suspended", "not being monitored"]),
        ("refuse to troubleshoot unmonitored equipment", ["do not troubleshoot"]),
        ("check history before repeating a repair", ["broken again", "needs replacing"]),
        ("ground every instruction", ["only the steps you are given", "never invent a step"]),
        ("stay in scope", ["billing", "you do not handle these"]),
        ("promise nothing", ["no dates", "cannot say when"]),
        ("never cut power to the panel", ["cut the power"]),
        ("emergencies come first", ["emergency services"]),
        ("ask rather than guess", ["say it again", "do not guess"]),
        ("hand over on request", ["without making them justify"]),
    ],
)
def test_every_rule_is_stated(rule, phrases):
    # The prompt is hard-wrapped prose, so a phrase can straddle a line break.
    text = re.sub(r"\s+", " ", system_prompt().lower())
    for phrase in phrases:
        assert phrase in text, f"the prompt does not state the rule about {rule}: {phrase!r}"


def test_prompt_is_written_to_be_spoken():
    text = system_prompt()
    # Anything the model might imitate in its own replies. It is being heard,
    # and a caller cannot hear a bullet point.
    for marker in ("http://", "https://", "|---", "```"):
        assert marker not in text, f"the prompt contains {marker!r}, which invites it back"


def test_grounding_block_names_its_sources():
    block = grounding_block(["1. Take the cover off."], ["Sensor offline"])
    assert "Sensor offline" in block
    assert "only instructions you may give" in block


def test_empty_retrieval_forbids_improvising():
    block = grounding_block([], [])
    assert "do not invent steps" in block.lower()
    assert "offer to get them a person" in block.lower()
