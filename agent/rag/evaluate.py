"""Measuring whether retrieval finds the right article.

Every answer the agent gives about fixing something is drawn from whatever
comes back here, so retrieval quality sets a ceiling on how good the agent can
be. A wrong article is worse than no article: the agent will confidently read
out steps for the wrong device.

Queries are written the way a caller speaks, not the way an article is titled.
Matching a query against the title it was derived from measures nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

from .retrieve import DEFAULT_TOP_K, Retriever

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Probe:
    query: str
    expected_slug: str
    device_type: str | None = None


# Phrased as a frustrated person on a phone would say it, including the vague
# ones. "It's not working" is a real thing callers say.
PROBES = [
    Probe("my front door sensor keeps saying offline", "door-window-sensor-offline"),
    Probe("the panel says the kitchen window is open but it's shut",
          "system-shows-not-ready"),
    Probe("something keeps setting off the alarm when nobody's home",
          "motion-sensor-false-alarms"),
    Probe("my alarm is going off right now and I can't stop it", "siren-will-not-stop"),
    Probe("the camera just shows a black screen", "camera-feed-black"),
    Probe("there's a beeping noise coming from the box in the hall", "panel-beeping"),
    Probe("it says low battery, what do I need to buy", "sensor-battery-replacement"),
    Probe("the keypad by the back door has gone dead", "keypad-not-responding"),
    Probe("the panel won't join my wifi", "control-panel-wifi"),
    Probe("I can't arm the system from my phone", "app-cannot-connect"),
    Probe("how do I set the alarm when I'm staying in tonight", "arming-and-disarming"),
    Probe("we had a power cut and now I'm not sure it's working",
          "power-outage-recovery"),
    Probe("my doorbell camera sends me an alert every time a car goes past",
          "outdoor-camera-motion-alerts"),
    Probe("the smoke alarm keeps chirping every minute", "smoke-detector-chirping"),
    Probe("the panel says tamper on the back door", "sensor-tamper-alert"),
    Probe("something in the lounge triggered when a glass smashed", "glass-break-sensor"),
    Probe("I need to change the battery in a window sensor", "low-battery-warning"),
]

# Questions the knowledge base has no answer to. The correct outcome is nothing
# above the floor, so the agent offers a person instead of improvising.
OUT_OF_SCOPE = [
    "I want to cancel my subscription",
    "why was I charged twice this month",
    "can you tell me what the weather is tomorrow",
    "I'd like to add my daughter to the account",
]


@dataclass
class Result:
    hits_at_1: int = 0
    hits_at_k: int = 0
    total: int = 0
    misses: list[tuple[str, str, str]] = None
    false_positives: list[tuple[str, str, float]] = None

    def __post_init__(self):
        self.misses = self.misses or []
        self.false_positives = self.false_positives or []

    @property
    def precision_at_1(self) -> float:
        return self.hits_at_1 / self.total if self.total else 0.0

    @property
    def recall_at_k(self) -> float:
        return self.hits_at_k / self.total if self.total else 0.0


async def evaluate(top_k: int = DEFAULT_TOP_K) -> Result:
    retriever = Retriever()
    result = Result()
    try:
        for probe in PROBES:
            passages = await retriever.search(
                probe.query, top_k=top_k, device_type=probe.device_type
            )
            result.total += 1
            slugs = [p.article_slug for p in passages]
            if slugs and slugs[0] == probe.expected_slug:
                result.hits_at_1 += 1
            if probe.expected_slug in slugs:
                result.hits_at_k += 1
            else:
                result.misses.append(
                    (probe.query, probe.expected_slug, slugs[0] if slugs else "nothing")
                )

        for query in OUT_OF_SCOPE:
            passages = await retriever.search(query, top_k=top_k)
            if passages:
                result.false_positives.append(
                    (query, passages[0].article_slug, passages[0].similarity)
                )
    finally:
        await retriever.aclose()
    return result


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Score knowledge base retrieval.")
    parser.add_argument("-k", "--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    result = await evaluate(top_k=args.top_k)
    print(f"\n{result.total} probes, top_k={args.top_k}\n")
    print(f"  precision@1  {result.precision_at_1:.0%}  "
          f"({result.hits_at_1}/{result.total} put the right article first)")
    print(f"  recall@{args.top_k}     {result.recall_at_k:.0%}  "
          f"({result.hits_at_k}/{result.total} found it at all)")

    if result.misses:
        print(f"\n  {len(result.misses)} not found:")
        for query, expected, got in result.misses:
            print(f'    "{query}"\n      wanted {expected}, top hit was {got}')

    print(f"\n  {len(OUT_OF_SCOPE)} out-of-scope questions, "
          f"{len(result.false_positives)} wrongly matched")
    for query, slug, score in result.false_positives:
        print(f'    "{query}" -> {slug} ({score:.3f})')


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
