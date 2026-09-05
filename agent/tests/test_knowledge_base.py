"""Checks on the troubleshooting articles.

The agent reads these aloud a step at a time and waits for the caller to
confirm before moving on, so the constraints that matter are about how the text
sounds rather than how it reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KB_DIR = Path(__file__).resolve().parents[1] / "rag" / "kb"
ARTICLES = sorted(KB_DIR.glob("*.md"))

# Long enough to be unspeakable in one breath, which is where a caller loses
# track of what they are being asked to do.
MAX_STEP_WORDS = 45


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def steps(path: Path) -> list[str]:
    body = re.sub(r"^---\n.*?\n---\n", "", path.read_text(), flags=re.S)
    return [m.group(1).strip() for m in re.finditer(r"^\d+\.\s+(.*)$", body, re.M)]


def test_there_are_enough_articles():
    assert len(ARTICLES) >= 15, f"only {len(ARTICLES)} articles"


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_article_declares_what_it_covers(path):
    fields = frontmatter(path)
    assert fields.get("title")
    assert fields.get("device_type")
    # Retrieval matches a caller's description of a problem, not an article
    # title, so each one has to carry the words a caller would actually use.
    assert len(fields.get("symptoms", "").split(",")) >= 3


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_article_has_numbered_steps(path):
    assert len(steps(path)) >= 3


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_every_step_is_short_enough_to_say(path):
    for step in steps(path):
        assert len(step.split()) <= MAX_STEP_WORDS, f"{path.name}: too long to speak: {step[:60]}"


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_steps_avoid_pointing_at_things_the_caller_cannot_see(path):
    # The caller is on a phone call. They cannot be shown a diagram or told to
    # look at the picture below.
    forbidden = ("see the diagram", "as shown", "pictured", "screenshot", "click here", "below:")
    body = path.read_text().lower()
    for phrase in forbidden:
        assert phrase not in body, f"{path.name} refers to something a caller cannot see: {phrase}"


def test_urgent_articles_deal_with_the_noise_first():
    # A caller with a siren going cannot hold a conversation until it stops.
    siren = KB_DIR / "siren-will-not-stop.md"
    first_step = steps(siren)[0].lower()
    assert "code" in first_step, "the first instruction should be the one that stops the noise"


def test_no_article_advises_cutting_power_to_the_panel():
    # Pulling the panel's power removes the backup battery's protection and can
    # leave a house unmonitored without the caller realising.
    for path in ARTICLES:
        body = path.read_text().lower()
        if "never advise" in body or "never cut" in body:
            continue
        assert "unplug the panel" not in body, f"{path.name} tells the caller to unplug the panel"
