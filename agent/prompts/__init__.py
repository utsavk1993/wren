"""The agent's instructions, kept as text rather than embedded in code.

Prompt wording is changed far more often than the code around it, usually in
response to something a caller said, and keeping it in files means those changes
show up as readable diffs rather than buried string edits.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

# A long prompt is paid for on every single turn, in the time before the first
# token comes back. This is the ceiling that keeps that cost visible: crossing
# it should be a decision, not an accident.
MAX_SYSTEM_PROMPT_WORDS = 1200


@lru_cache(maxsize=None)
def system_prompt() -> str:
    text = (PROMPT_DIR / "system.md").read_text().strip()
    words = len(text.split())
    if words > MAX_SYSTEM_PROMPT_WORDS:
        raise ValueError(
            f"the system prompt is {words} words, over the {MAX_SYSTEM_PROMPT_WORDS} "
            "budget; every turn pays for this before it can start speaking"
        )
    return text


def grounding_block(passages: list[str], titles: list[str]) -> str:
    """Wrap retrieved steps so it is clear they are the only permitted source.

    Placed after the conversation rather than inside the system prompt, because
    what is retrieved changes with the problem while the instructions do not,
    and a stable prefix is what makes prompt caching worth having.
    """
    if not passages:
        return (
            "NO TROUBLESHOOTING STEPS WERE FOUND for what the caller described.\n"
            "You have nothing to work from. Do not invent steps and do not adapt "
            "instructions for a different problem. Tell the caller you do not have "
            "a fix for this and offer to get them a person."
        )
    sections = "\n\n".join(
        f"--- from: {title} ---\n{passage}" for title, passage in zip(titles, passages)
    )
    return (
        "TROUBLESHOOTING STEPS. These are the only instructions you may give. "
        "If they do not cover what the caller is describing, say so rather than "
        "filling the gap yourself.\n\n" + sections
    )
