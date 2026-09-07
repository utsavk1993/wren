"""Loading the knowledge base into the vector store.

Re-running replaces what is there rather than adding to it, so an edited
article does not leave its previous wording behind to be retrieved later.
"""

from __future__ import annotations

import asyncio
import logging
import os

import numpy as np

from .chunking import load_chunks
from .connection import connect
from .embeddings import get_embedder

log = logging.getLogger(__name__)


async def ingest() -> dict[str, int]:
    chunks = load_chunks()
    embedder = get_embedder()
    try:
        vectors = await embedder.embed([c.content for c in chunks])
    finally:
        close = getattr(embedder, "aclose", None)
        if close:
            await close()

    if len(vectors) != len(chunks):
        raise RuntimeError(f"expected {len(chunks)} vectors, received {len(vectors)}")
    if vectors and len(vectors[0]) != embedder.dimensions:
        raise RuntimeError(
            f"embedding width {len(vectors[0])} does not match the schema's "
            f"{embedder.dimensions}; the table needs rebuilding for this model"
        )

    with connect() as conn:
        with conn.cursor() as cur:
            # Replacing wholesale rather than upserting: an article that loses a
            # section would otherwise keep the old text in the index, and it
            # would go on being retrieved and read out.
            cur.execute("DELETE FROM kb_chunks")
            cur.executemany(
                """
                INSERT INTO kb_chunks (
                    article_slug, article_title, device_type, chunk_index, content, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        c.article_slug, c.article_title, c.device_type,
                        c.chunk_index, c.content, np.asarray(v, dtype=np.float32),
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
        conn.commit()
        total = conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0]

    log.info("ingested %d chunks", total)
    return {"articles": len({c.article_slug for c in chunks}), "chunks": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(ingest()))
