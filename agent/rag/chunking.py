"""Splitting articles into the pieces that get embedded and retrieved.

Articles are split at their numbered steps rather than at a fixed number of
tokens. A fixed window cuts wherever it happens to land, which regularly lands
in the middle of an instruction: retrieval then returns half a step, and the
agent reads out something the caller cannot act on.

Grouping consecutive steps keeps a procedure intact while staying small enough
that retrieval is precise. Each chunk carries its article's title and symptoms,
because the words a caller uses to describe a problem appear there rather than
in the steps themselves, which are written as instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KB_DIR = Path(__file__).parent / "kb"

# Roughly how many words to gather before starting a new chunk. Steps are short,
# so this works out at a handful of instructions each: enough context for a
# procedure to make sense, small enough that a match is specific.
TARGET_WORDS = 110

# The last step of one chunk is repeated at the top of the next, so a procedure
# split across two chunks does not lose its thread at the seam.
OVERLAP_STEPS = 1


@dataclass(frozen=True)
class Chunk:
    article_slug: str
    article_title: str
    device_type: str
    chunk_index: int
    content: str


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    device_type: str
    symptoms: str
    also_applies_to: str
    body: str


def parse_article(path: Path) -> Article:
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        raise ValueError(f"{path.name} has no frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return Article(
        slug=path.stem,
        title=fields.get("title", path.stem),
        device_type=fields.get("device_type", ""),
        symptoms=fields.get("symptoms", ""),
        also_applies_to=fields.get("also_applies_to", ""),
        body=match.group(2).strip(),
    )


def _segments(body: str) -> list[str]:
    """Break a body into prose paragraphs and individual numbered steps.

    Steps stay whole. Prose between them is kept because it explains when a
    procedure applies, which is often what makes one article the right match
    rather than another.
    """
    segments: list[str] = []
    buffer: list[str] = []

    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s", stripped):
            if buffer:
                joined = " ".join(buffer).strip()
                if joined:
                    segments.append(joined)
                buffer = []
            segments.append(stripped)
        elif not stripped:
            if buffer:
                joined = " ".join(buffer).strip()
                if joined:
                    segments.append(joined)
                buffer = []
        else:
            buffer.append(stripped)

    if buffer:
        joined = " ".join(buffer).strip()
        if joined:
            segments.append(joined)
    return segments


def _is_step(segment: str) -> bool:
    return bool(re.match(r"^\d+\.\s", segment))


def chunk_article(article: Article) -> list[Chunk]:
    header = f"{article.title}\nSymptoms: {article.symptoms}"
    segments = _segments(article.body)

    grouped: list[list[str]] = []
    current: list[str] = []
    words = 0
    # The most recent line of prose, which is what says when the steps that
    # follow it apply.
    context: str | None = None

    for segment in segments:
        segment_words = len(segment.split())
        if current and words + segment_words > TARGET_WORDS:
            grouped.append(current)
            # Carry the tail of the previous group forward so a procedure that
            # spans the boundary still reads as one sequence.
            carried = current[-OVERLAP_STEPS:] if OVERLAP_STEPS else []
            # A chunk that opens at step five, with nothing saying what those
            # steps are for, reads as a plausible answer to the wrong question.
            # Bring the sentence that introduced the procedure with it.
            if carried and _is_step(carried[0]) and context and context not in carried:
                carried = [context, *carried]
            elif not carried and context:
                carried = [context]
            current = carried
            words = sum(len(s.split()) for s in current)
        if not _is_step(segment):
            context = segment
        current.append(segment)
        words += segment_words

    if current:
        grouped.append(current)

    return [
        Chunk(
            article_slug=article.slug,
            article_title=article.title,
            device_type=article.device_type,
            chunk_index=index,
            # The title and symptoms lead every chunk. Retrieval compares a
            # caller's description of a problem against this text, and that
            # description looks far more like the symptoms than the steps.
            content=f"{header}\n\n" + "\n".join(group),
        )
        for index, group in enumerate(grouped)
    ]


def load_chunks(kb_dir: Path = KB_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        chunks.extend(chunk_article(parse_article(path)))
    return chunks


if __name__ == "__main__":
    chunks = load_chunks()
    print(f"{len(chunks)} chunks from {len(list(KB_DIR.glob('*.md')))} articles")
    sizes = [len(c.content.split()) for c in chunks]
    print(f"words per chunk: min {min(sizes)}, median {sorted(sizes)[len(sizes)//2]}, max {max(sizes)}")
