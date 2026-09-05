"""Running the scenarios and scoring what happened.

Each scenario is a whole call against the real model and the real connected
systems, so what is measured is the agent as it would actually behave, not a
rehearsal of it.

Scoring is on behaviour: which tools were reached for, which rules refused, and
whether anything forbidden was said. Wording is deliberately not checked. Two
different phrasings of the same correct refusal are both correct, and pinning
sentences would turn every prompt change into a false regression.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field

from conversation import Conversation
from eval.scenarios import SCENARIOS, Scenario
from llm import get_model
from rag.retrieve import Retriever
from systems.salesforce import SalesforceClient
from systems.telemetry import TelemetryClient
from tools.dispatch import Dispatcher

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    denials: list[str] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    total_ms: float = 0.0


def _score(scenario: Scenario, convo: Conversation) -> list[str]:
    failures: list[str] = []
    used = {c["name"] for c in convo.record.tool_calls}
    denials = set(convo.record.denials)
    said = " ".join(t.text for t in convo.record.turns if t.speaker == "agent").lower()

    for tool in scenario.expect_tools - used:
        failures.append(f"never called {tool}")
    for tool in scenario.forbid_tools & used:
        failures.append(f"called {tool}, which it should not have")
    for rule in scenario.expect_denials - denials:
        failures.append(f"the {rule} rule never refused anything")
    for forbidden in scenario.forbid_text:
        if forbidden.lower() in said:
            failures.append(f"said {forbidden!r} out loud")
    if scenario.must_not_invent_steps:
        lookups = [c for c in convo.record.tool_calls if c["name"] == "look_up_steps"]
        invented = [
            c for c in lookups
            if c["result"].get("steps_found") and "refused" not in c["result"]
        ]
        if invented:
            failures.append(
                "found steps for something the knowledge base does not cover"
            )
    if scenario.must_reach_a_person and not (
        convo.state.caller_requested_human or "hand_to_a_person" in used
    ):
        failures.append("never handed the call to a person")
    return failures


async def run_one(scenario: Scenario) -> Outcome:
    salesforce, telemetry, retriever = (
        SalesforceClient(), TelemetryClient(), Retriever()
    )
    convo = Conversation(get_model(), Dispatcher(salesforce, telemetry, retriever))
    try:
        for line in scenario.lines:
            await convo.say(line)
    except Exception as exc:  # noqa: BLE001 - a crash is a failing scenario, not a stop
        log.exception("scenario %s crashed", scenario.name)
        return Outcome(scenario.name, False, [f"crashed: {exc}"])
    finally:
        await salesforce.aclose()
        await telemetry.aclose()
        await retriever.aclose()

    failures = _score(scenario, convo)
    return Outcome(
        name=scenario.name,
        passed=not failures,
        failures=failures,
        tools_used=[c["name"] for c in convo.record.tool_calls],
        denials=convo.record.denials,
        turns=[{"speaker": t.speaker, "text": t.text} for t in convo.record.turns],
        total_ms=sum(float(t.get("ms_total", 0)) for t in convo.record.timings),
    )


async def run_all(only: str | None = None, repeat: int = 1) -> list[Outcome]:
    chosen = [s for s in SCENARIOS if not only or only.lower() in s.name.lower()]
    # One at a time. Scenarios share the same customer records, and running them
    # together would let one call's writes change what another one sees.
    outcomes = []
    for scenario in chosen:
        for _ in range(repeat):
            outcomes.append(await run_one(scenario))
    return outcomes


def report(outcomes: list[Outcome], verbose: bool = False) -> int:
    # Grouped by scenario, because with more than one run per scenario the
    # interesting number is how often it holds, not whether it held once.
    by_name: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_name.setdefault(outcome.name, []).append(outcome)

    print()
    all_passed = True
    for name, runs in by_name.items():
        passed = sum(1 for r in runs if r.passed)
        rate = f"{passed}/{len(runs)}"
        mark = "pass" if passed == len(runs) else ("flaky" if passed else "FAIL")
        average = sum(r.total_ms for r in runs) / len(runs)
        print(f"  [{mark:5}] {rate}  {name}  ({average:.0f} ms)")
        for failure in sorted({f for r in runs for f in r.failures}):
            print(f"           {failure}")
        if passed != len(runs):
            all_passed = False
        if verbose:
            for turn in runs[0].turns:
                who = "caller" if turn["speaker"] == "caller" else "agent "
                print(f"           {who}: {turn['text'][:110]}")

    total_pass = sum(1 for o in outcomes if o.passed)
    print(f"\n  {total_pass}/{len(outcomes)} runs passed "
          f"across {len(by_name)} scenarios")
    if outcomes:
        print(f"  average call: {sum(o.total_ms for o in outcomes) / len(outcomes):.0f} ms")
    return 0 if all_passed else 1


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Score the agent against known cases.")
    parser.add_argument("-k", "--only", help="run scenarios matching this text")
    parser.add_argument("-v", "--verbose", action="store_true", help="print transcripts")
    parser.add_argument("--json", help="write the full results here")
    parser.add_argument(
        "-n", "--repeat", type=int, default=1,
        help="run each scenario this many times; the model is not deterministic "
             "and one run says less than it appears to",
    )
    args = parser.parse_args()

    outcomes = await run_all(args.only, repeat=args.repeat)
    if args.json:
        with open(args.json, "w") as handle:
            json.dump([asdict(o) for o in outcomes], handle, indent=2)
        print(f"  written to {args.json}")
    return report(outcomes, verbose=args.verbose)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(_main()))
