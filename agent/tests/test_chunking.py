"""Checks on how articles are split for retrieval."""

from __future__ import annotations

import re

import pytest

from rag.chunking import KB_DIR, chunk_article, load_chunks, parse_article

ARTICLES = sorted(KB_DIR.glob("*.md"))


def test_every_article_produces_chunks():
    chunks = load_chunks()
    assert len({c.article_slug for c in chunks}) == len(ARTICLES)


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_chunks_carry_the_words_a_caller_would_use(path):
    # Retrieval compares a caller's description of a problem against this text.
    # A caller says "my camera is black", not "camera feed diagnostics", so the
    # symptom phrases have to be inside the embedded content.
    article = parse_article(path)
    for chunk in chunk_article(article):
        assert article.title in chunk.content
        assert "Symptoms:" in chunk.content


@pytest.mark.parametrize("path", ARTICLES, ids=lambda p: p.stem)
def test_no_step_is_split_across_two_chunks(path):
    # A half instruction is worse than none: the agent would read out something
    # the caller cannot act on.
    article = parse_article(path)
    original = {
        m.group(0).strip() for m in re.finditer(r"^\d+\.\s+.*$", article.body, re.M)
    }
    combined = "\n".join(c.content for c in chunk_article(article))
    for step in original:
        assert step in combined, f"{path.stem}: step lost or split: {step[:50]}"


def test_chunks_stay_within_a_useful_size():
    for chunk in load_chunks():
        words = len(chunk.content.split())
        assert 20 <= words <= 220, f"{chunk.article_slug}#{chunk.chunk_index}: {words} words"


def _body_lines(chunk) -> list[str]:
    """The chunk's own lines, without the title and symptom header."""
    return [ln.strip() for ln in chunk.content.split("\n\n", 1)[-1].splitlines() if ln.strip()]


def test_consecutive_chunks_overlap():
    # A procedure split across a boundary should not lose its thread at the
    # seam. The carried line is often the sentence introducing the steps rather
    # than a step itself, which is the more useful thing to keep: instructions
    # without the condition they apply under are not actionable.
    by_article: dict[str, list] = {}
    for chunk in load_chunks():
        by_article.setdefault(chunk.article_slug, []).append(chunk)
    multi = [v for v in by_article.values() if len(v) > 1]
    assert multi, "expected at least one article to need more than one chunk"
    for chunks in multi:
        for first, second in zip(chunks, chunks[1:]):
            assert set(_body_lines(first)) & set(_body_lines(second)), (
                f"{first.article_slug}: nothing carried across the boundary"
            )


def test_a_chunk_that_opens_with_steps_says_what_they_are_for():
    # A chunk beginning at step four, with no indication of when those steps
    # apply, retrieves as a plausible answer to the wrong question.
    for chunk in load_chunks():
        lines = _body_lines(chunk)
        if not lines or not re.match(r"^\d+\.", lines[0]):
            continue
        assert not lines[0].startswith("1."), "a first step needs no preamble"
        pytest.fail(
            f"{chunk.article_slug}#{chunk.chunk_index} opens mid-procedure "
            f"without context: {lines[0][:60]}"
        )


def test_chunk_indexes_are_sequential():
    by_article: dict[str, list[int]] = {}
    for chunk in load_chunks():
        by_article.setdefault(chunk.article_slug, []).append(chunk.chunk_index)
    for slug, indexes in by_article.items():
        assert sorted(indexes) == list(range(len(indexes))), slug
