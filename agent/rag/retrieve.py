"""Finding the troubleshooting steps that match what a caller has described.

Everything the agent says about how to fix something comes from here. If
nothing relevant is found, that is an answer in itself: the agent says it does
not have a fix and offers a person, rather than inventing steps.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass

import numpy as np

from .connection import connect
from .embeddings import get_embedder

log = logging.getLogger(__name__)

DEFAULT_TOP_K = 4

# Below this, the closest match is not about the caller's problem. Saying so is
# the correct answer; reading out the nearest article anyway is how an agent
# ends up confidently giving instructions for the wrong device.
#
# Chosen from the measured spread rather than by feel. Across the evaluation
# probes, passages from the right article score no lower than 0.61, while
# questions the knowledge base cannot answer peak at 0.59. This sits in that
# gap, and the evaluation asserts the gap still exists.
MIN_SIMILARITY = 0.60


@dataclass(frozen=True)
class Passage:
    article_slug: str
    article_title: str
    device_type: str
    content: str
    similarity: float


class Retriever:
    def __init__(self, database_url: str | None = None) -> None:
        self._embedder = get_embedder()

    async def aclose(self) -> None:
        close = getattr(self._embedder, "aclose", None)
        if close:
            await close()

    async def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        device_type: str | None = None,
        min_similarity: float = MIN_SIMILARITY,
    ) -> list[Passage]:
        """Passages matching a caller's description, closest first.

        Passing the device type narrows the search to equipment the household
        actually owns, which stops a question about a door sensor matching an
        article about a camera purely because both mention going offline.
        """
        if not query.strip():
            return []

        vectors = await self._embedder.embed([query])
        # A plain list of floats adapts to a Postgres array rather than a
        # vector, which the distance operator will not accept.
        embedding = np.asarray(vectors[0], dtype=np.float32)

        # Cosine distance runs 0 to 2, so similarity is one minus it. Direction
        # is what carries meaning in an embedding; magnitude does not.
        sql = """
            SELECT article_slug, article_title, device_type, content,
                   1 - (embedding <=> %(embedding)s) AS similarity
            FROM kb_chunks
            WHERE embedding IS NOT NULL
        """
        params: dict[str, object] = {"embedding": embedding, "top_k": top_k}
        if device_type:
            # Articles with no declared type are general advice and stay in.
            sql += " AND (device_type = %(device_type)s OR device_type IS NULL OR device_type = '')"
            params["device_type"] = device_type
        sql += " ORDER BY embedding <=> %(embedding)s LIMIT %(top_k)s"

        def run() -> list[dict]:
            with connect(rows_as_dicts=True) as conn:
                return conn.execute(sql, params).fetchall()

        rows = await asyncio.to_thread(run)
        passages = [
            Passage(
                article_slug=r["article_slug"],
                article_title=r["article_title"],
                device_type=r["device_type"] or "",
                content=r["content"],
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]
        return [p for p in passages if p.similarity >= min_similarity]


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the troubleshooting knowledge base the way the agent does."
    )
    parser.add_argument("query", help="what the caller said")
    parser.add_argument("-k", "--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("-d", "--device-type", default=None)
    parser.add_argument("--min-similarity", type=float, default=MIN_SIMILARITY)
    parser.add_argument("--full", action="store_true", help="print whole passages")
    args = parser.parse_args()

    retriever = Retriever()
    try:
        passages = await retriever.search(
            args.query,
            top_k=args.top_k,
            device_type=args.device_type,
            min_similarity=args.min_similarity,
        )
    finally:
        await retriever.aclose()

    print(f'\nquery: "{args.query}"')
    if args.device_type:
        print(f"filtered to: {args.device_type}")
    if not passages:
        print("\nnothing above the similarity floor. The agent would offer a person here.")
        return
    print()
    for rank, passage in enumerate(passages, start=1):
        print(f"{rank}. {passage.similarity:.3f}  {passage.article_title}  [{passage.article_slug}]")
        body = passage.content.split("\n\n", 1)[-1]
        print("     " + (body if args.full else body[:160].replace("\n", " ") + "..."))
        print()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
    asyncio.run(_main())
