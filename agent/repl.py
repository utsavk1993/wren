"""Talk to the agent by typing, before there is any audio.

The conversation is hard enough to get right on its own. Debugging it at the
same time as speech recognition and turn taking means never knowing which of
the two is at fault, so it is worth being able to hold a whole call in text
first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

import policy
from conversation import Conversation, Turn
from llm import get_model
from rag.retrieve import Retriever
from systems.salesforce import SalesforceClient
from systems.telemetry import TelemetryClient
from tools.dispatch import Dispatcher

GREETING = (
    "Thanks for calling. My name is Wren. Can I start with the phone number "
    "on your account?"
)

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _show_state(state: policy.CallState) -> None:
    customer = state.customer
    print(f"{DIM}  caller:   {customer.full_name if customer else '(unidentified)'}")
    print(f"  verified: {state.verified}  attempts: {state.verification_attempts}")
    if customer:
        print(f"  account:  {customer.external_id}  {customer.plan}  {customer.status.value}")
    if state.device_under_discussion:
        d = state.device_under_discussion
        print(f"  device:   {d.name} ({d.external_id}) {d.status.value}")
    print(f"  steps given: {len(state.steps_given)}{RESET}")


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Hold a call with the agent in text.")
    parser.add_argument("--show-tools", action="store_true",
                        help="print every tool call and what came back")
    parser.add_argument("--transcript", help="write the call record here when it ends")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set, so there is no model to talk to.",
              file=sys.stderr)
        return 1

    salesforce = SalesforceClient()
    telemetry = TelemetryClient()
    retriever = Retriever()
    conversation = Conversation(
        get_model(), Dispatcher(salesforce, telemetry, retriever)
    )

    print(f"\n{BOLD}wren{RESET} {DIM}(ctrl-d to hang up, /state to inspect){RESET}\n")
    print(f"{BOLD}agent:{RESET} {GREETING}\n")
    # The agent speaks first, the way it would when a call connects.
    conversation.record.turns.append(Turn("agent", GREETING))

    already_seen = 0
    try:
        while True:
            try:
                said = input(f"{BOLD}you:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not said:
                continue
            if said == "/state":
                _show_state(conversation.state)
                continue

            reply = await conversation.say(said)

            if args.show_tools:
                for call in conversation.record.tool_calls[already_seen:]:
                    result = dict(call["result"])
                    guidance = result.pop("guidance", None)
                    print(f"{DIM}  -> {call['name']}: {result}")
                    if guidance:
                        print(f"     {guidance}{RESET}")
                    else:
                        print(RESET, end="")
                already_seen = len(conversation.record.tool_calls)

            print(f"\n{BOLD}agent:{RESET} {reply}\n")
    finally:
        await salesforce.aclose()
        await telemetry.aclose()
        await retriever.aclose()

    record = conversation.record
    print(f"{DIM}call {record.id}: {len(record.turns)} turns, "
          f"{len(record.tool_calls)} tool calls, "
          f"{len(record.denials)} refusals{RESET}")
    if record.denials:
        print(f"{DIM}refused: {', '.join(sorted(set(record.denials)))}{RESET}")

    if args.transcript:
        with open(args.transcript, "w") as handle:
            json.dump(
                {
                    "id": record.id,
                    "turns": [t.__dict__ for t in record.turns],
                    "tool_calls": record.tool_calls,
                    "denials": record.denials,
                },
                handle,
                indent=2,
                default=str,
            )
        print(f"{DIM}transcript written to {args.transcript}{RESET}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
    raise SystemExit(asyncio.run(_main()))
