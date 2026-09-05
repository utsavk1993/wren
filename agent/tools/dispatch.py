"""Deciding whether a proposed tool call may run, and running it.

The model proposes; this decides. Every call is checked against the rules and
against the state of the call before it reaches a connected system, so a model
that asks for something it should not have is refused rather than obeyed.

A refusal is returned to the model as an ordinary result, with the reason. That
keeps the conversation going in the right direction: the model finds out what
it should be doing instead, rather than being cut off mid-call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import policy
from llm import ToolCall
from rag.retrieve import Retriever
from systems.models import ONLINE, DeviceStatus
from systems.salesforce import SalesforceClient
from systems.telemetry import TelemetryClient, TelemetryError

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        salesforce: SalesforceClient,
        telemetry: TelemetryClient,
        retriever: Retriever,
    ) -> None:
        self.salesforce = salesforce
        self.telemetry = telemetry
        self.retriever = retriever
        self.last_passages: list[str] = []
        self.last_titles: list[str] = []

    async def run(self, call: ToolCall, state: policy.CallState) -> dict[str, Any]:
        handler = getattr(self, f"_{call.name}", None)
        if handler is None:
            return {"error": f"there is no tool called {call.name}"}
        try:
            return await handler(call.arguments or {}, state)
        except TelemetryError as exc:
            log.warning("telemetry unavailable: %s", exc)
            return {
                "error": "the equipment system is not responding",
                "guidance": (
                    "Tell the caller you cannot see their equipment at the moment and "
                    "offer to get them a person. Do not guess at its state."
                ),
            }
        except Exception:
            log.exception("tool %s failed", call.name)
            return {"error": "that lookup failed", "guidance": "Offer to get them a person."}

    # ---- identification and verification ----

    async def _find_customer(self, args: dict, state: policy.CallState) -> dict:
        phone = str(args.get("phone", ""))
        customer = await self.salesforce.find_customer_by_phone(phone)
        if customer is None:
            return {
                "found": False,
                "guidance": (
                    "No account uses that number. Ask whether they might be calling "
                    "from a different phone, and ask for the number on the account."
                ),
            }
        state.customer = customer
        # Nothing identifying is returned yet. The model has not established
        # that this caller is entitled to hear any of it.
        return {
            "found": True,
            "guidance": "Now ask for the four digit passcode on the account.",
        }

    async def _check_passcode(self, args: dict, state: policy.CallState) -> dict:
        if state.customer is None:
            return {"error": "nobody has been looked up yet"}
        if state.verification_exhausted:
            return {
                "verified": False,
                "attempts_remaining": 0,
                "guidance": (
                    "Two attempts have already failed. Do not accept another. Offer a "
                    "callback to the number on the account and end the call politely."
                ),
            }

        state.verification_attempts += 1
        ok = await self.salesforce.verify_passcode(
            state.customer.account_id, str(args.get("spoken", ""))
        )
        state.verified = ok
        if ok:
            customer = state.customer
            # Everything the rest of the call will need is fetched now, at the
            # one moment the caller is already expecting a pause, and fetched
            # together rather than one after another. Each separate lookup later
            # would cost another round trip to the model, and the caller waits
            # through every one of them.
            history, devices = await asyncio.gather(
                self.salesforce.get_case_history(customer.external_id),
                self._devices_or_empty(customer.external_id),
            )
            state.history = history
            state.devices = devices
            return {
                "verified": True,
                "name": customer.first_name,
                "plan": customer.plan,
                "account_status": customer.status.value,
                "monitored": customer.status.is_monitored,
                "equipment": [
                    {
                        "id": d.external_id,
                        "name": d.name,
                        "type": d.device_type.replace("_", " "),
                        "reporting": d.status == ONLINE,
                    }
                    for d in devices
                ] if customer.status.is_monitored else [],
                "guidance": (
                    "Greet them by name and ask what is wrong."
                    if customer.status.is_monitored
                    else (
                        f"This account is {customer.status.value.lower()}, so the "
                        "equipment is not being monitored. Say that plainly, do not "
                        "troubleshoot, and offer the team who can restart the service."
                    )
                ),
            }

        remaining = policy.MAX_VERIFICATION_ATTEMPTS - state.verification_attempts
        return {
            "verified": False,
            "attempts_remaining": max(remaining, 0),
            "guidance": (
                "That was not right. Say so plainly and let them try once more."
                if remaining > 0
                else (
                    "That was the second failure. Do not confirm anything about the "
                    "account. Offer a callback to the number already on file."
                )
            ),
            # Saying what was wrong with a guess narrows the next one.
            "do_not_say": "anything about how close the answer was",
        }

    async def _devices_or_empty(self, customer_external_id: str):
        """Equipment, or nothing if the platform is not answering.

        Failing here must not sink the whole verification step: knowing who the
        caller is still lets the call continue.
        """
        try:
            return await self.telemetry.get_devices(customer_external_id)
        except TelemetryError as exc:
            log.warning("could not prefetch equipment: %s", exc)
            return []

    # ---- equipment ----

    async def _list_equipment(self, args: dict, state: policy.CallState) -> dict:
        allowed = policy.may_disclose_account_details(state)
        if not allowed:
            return {"refused": allowed.reason.value, "guidance": allowed.guidance}
        assert state.customer is not None

        devices = await self.telemetry.get_devices(state.customer.external_id)
        state.devices = devices
        return {
            "equipment": [
                {
                    "id": d.external_id,
                    "name": d.name,
                    "type": d.device_type.replace("_", " "),
                    "reporting": d.status == ONLINE,
                    "status": d.status,
                    "battery_pct": d.battery_pct,
                }
                for d in devices
            ],
            "guidance": (
                "Anything not reporting is listed first. Ask the caller which one "
                "they are calling about if it is not obvious."
            ),
        }

    async def _recheck_equipment(self, args: dict, state: policy.CallState) -> dict:
        allowed = policy.may_disclose_account_details(state)
        if not allowed:
            return {"refused": allowed.reason.value, "guidance": allowed.guidance}

        device_id = str(args.get("device_external_id", ""))
        device = await self.telemetry.get_device(device_id)
        if device is None:
            return {"error": "no equipment with that identifier"}

        # Whether a reset actually worked is decided by the equipment, not by
        # the caller reporting that they did the step.
        if device.status != ONLINE and device.recovers_on_reset:
            device = await self.telemetry.set_device_status(device_id, ONLINE)

        state.device_under_discussion = device
        return {
            "id": device.external_id,
            "name": device.name,
            "reporting": device.status == ONLINE,
            "guidance": (
                "It is reporting again. Confirm that with the caller and close the call."
                if device.status == ONLINE
                else "Still not reporting. Say so honestly rather than trying again."
            ),
        }

    # ---- knowledge base ----

    async def _look_up_steps(self, args: dict, state: policy.CallState) -> dict:
        device_id = args.get("device_external_id")
        if device_id:
            match = next((d for d in state.devices if d.external_id == device_id), None)
            if match:
                state.device_under_discussion = match

        allowed = policy.may_troubleshoot(state)
        if not allowed:
            return {"refused": allowed.reason.value, "guidance": allowed.guidance}

        device = state.device_under_discussion
        passages = await self.retriever.search(
            str(args.get("problem", "")),
            device_type=device.device_type if device else None,
        )
        grounded = policy.may_give_these_steps(state, [p.content for p in passages])
        if not grounded:
            self.last_passages, self.last_titles = [], []
            return {"steps_found": False, "guidance": grounded.guidance}

        self.last_passages = [p.content for p in passages]
        self.last_titles = [p.article_title for p in passages]
        state.steps_given.append(str(args.get("problem", "")))
        return {
            "steps_found": True,
            "sources": self.last_titles,
            "guidance": (
                "The steps are supplied separately. Give one at a time and wait for "
                "the caller each time. Do not add anything that is not in them."
            ),
        }

    # ---- handing off ----

    async def _open_case(self, args: dict, state: policy.CallState) -> dict:
        allowed = policy.may_disclose_account_details(state)
        if not allowed:
            return {"refused": allowed.reason.value, "guidance": allowed.guidance}
        assert state.customer is not None

        case_number = await self.salesforce.create_case(
            account_id=state.customer.account_id,
            contact_id=state.customer.contact_id,
            subject=str(args.get("summary", "Reported fault")),
            description=str(args.get("detail", "")),
            device_external_id=args.get("device_external_id"),
        )
        state.case_number = case_number
        return {
            "case_number": case_number,
            "guidance": (
                "Read the case number back to the caller. Do not say when anyone will "
                "call, because you do not know."
            ),
        }

    async def _hand_to_a_person(self, args: dict, state: policy.CallState) -> dict:
        reason = str(args.get("reason", "unspecified"))
        case_number = args.get("case_number") or state.case_number
        if case_number:
            await self.salesforce.escalate_case(case_number, reason)
        state.caller_requested_human = True
        return {
            "handed_off": True,
            "case_number": case_number,
            "guidance": (
                "Tell them a member of the team will call them back on the number on "
                "the account. Do not promise a time."
            ),
        }
