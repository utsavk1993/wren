"""Checks that retrieval finds the right steps and stays quiet when it cannot.

Retrieval sets a ceiling on the whole agent: everything it says about fixing
something comes from what is returned here. Returning the wrong article is
worse than returning nothing, because the agent will read the wrong
instructions out with the same confidence as the right ones.
"""

from __future__ import annotations

import os

import pytest

from rag.evaluate import OUT_OF_SCOPE, PROBES, evaluate
from rag.retrieve import Retriever

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs an ingested knowledge base",
)


@pytest.fixture(scope="module")
async def retriever():
    r = Retriever()
    yield r
    await r.aclose()


async def test_the_right_article_is_always_found(retriever):
    result = await evaluate(top_k=4)
    assert result.recall_at_k == 1.0, f"missed: {result.misses}"


async def test_most_queries_rank_the_right_article_first(retriever):
    result = await evaluate(top_k=4)
    # Everything in the top four reaches the model, so a near miss on the first
    # position is survivable. A sustained drop is not.
    assert result.precision_at_1 >= 0.70


async def test_questions_the_knowledge_base_cannot_answer_return_nothing(retriever):
    for query in OUT_OF_SCOPE:
        passages = await retriever.search(query)
        assert passages == [], f"{query!r} matched {passages[0].article_slug if passages else ''}"


async def test_the_similarity_floor_still_separates_signal_from_noise(retriever):
    """The floor is a measured value, so the measurement has to keep holding.

    If new articles narrow the gap between a real match and an unanswerable
    question, the floor stops working and this fails rather than quietly
    admitting nonsense.
    """
    correct, noise = [], []
    for probe in PROBES:
        for passage in await retriever.search(probe.query, top_k=4, min_similarity=0.0):
            if passage.article_slug == probe.expected_slug:
                correct.append(passage.similarity)
    for query in OUT_OF_SCOPE:
        for passage in await retriever.search(query, top_k=4, min_similarity=0.0):
            noise.append(passage.similarity)
    assert min(correct) > max(noise), (
        f"real matches now reach as low as {min(correct):.3f} while unanswerable "
        f"questions reach {max(noise):.3f}; the floor no longer separates them"
    )


async def test_empty_query_returns_nothing(retriever):
    assert await retriever.search("") == []
    assert await retriever.search("   ") == []


async def test_device_type_filter_narrows_results(retriever):
    unfiltered = await retriever.search("it keeps going offline", top_k=6, min_similarity=0.0)
    filtered = await retriever.search(
        "it keeps going offline", top_k=6, device_type="camera", min_similarity=0.0
    )
    assert filtered
    assert all(p.device_type in ("camera", "") for p in filtered)
    assert {p.article_slug for p in filtered} != {p.article_slug for p in unfiltered}


async def test_results_come_back_ordered(retriever):
    passages = await retriever.search("my camera shows a black screen", top_k=4)
    scores = [p.similarity for p in passages]
    assert scores == sorted(scores, reverse=True)
