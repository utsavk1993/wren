"""The tools the model may call, and their argument schemas.

Schemas are strict so arguments arrive already valid. That is a convenience,
not a safeguard: the model proposes a call and this side decides whether to
make it, so every argument is checked again before anything is executed.
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_customer",
        "description": (
            "Look up the household behind a phone number. Call this once the caller "
            "has read their number out. Returns nothing if no account uses that number."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "The phone number exactly as the caller said it.",
                }
            },
            "required": ["phone"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_passcode",
        "description": (
            "Check the four digit passcode the caller has just spoken. Returns only "
            "whether it was right. You are never told the correct value."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "spoken": {
                    "type": "string",
                    "description": "The digits the caller said.",
                }
            },
            "required": ["spoken"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_equipment",
        "description": (
            "The equipment installed at this household and whether each piece is "
            "currently reporting. Only available once the caller is verified."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "look_up_steps",
        "description": (
            "Find the troubleshooting steps for what the caller has described. Use "
            "their own words. If nothing comes back there is no fix to offer and you "
            "must say so rather than inventing one."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "problem": {
                    "type": "string",
                    "description": "What the caller said is wrong, in their words.",
                },
                "device_external_id": {
                    "type": "string",
                    "description": "The piece of equipment being discussed, if known.",
                },
            },
            "required": ["problem"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recheck_equipment",
        "description": (
            "Ask the equipment whether it is reporting again, after the caller has "
            "carried out a step. Use this to confirm a fix rather than assuming it."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "device_external_id": {"type": "string"},
            },
            "required": ["device_external_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_case",
        "description": (
            "Open a support case. Returns a case number to read back to the caller. "
            "Use when the problem cannot be fixed on this call."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One line describing the fault.",
                },
                "detail": {
                    "type": "string",
                    "description": "What was tried and what happened.",
                },
                "device_external_id": {"type": "string"},
            },
            "required": ["summary", "detail"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hand_to_a_person",
        "description": (
            "Mark the call for a person to pick up. Use when the caller asks for one, "
            "when the account is not being monitored, when equipment needs replacing, "
            "or when the request is not about broken equipment."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this needs a person.",
                },
                "case_number": {
                    "type": "string",
                    "description": "An existing case number, if one has been opened.",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]
