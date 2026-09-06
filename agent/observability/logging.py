"""Structured logs, with the things that must not be logged taken out.

Every turn writes one record carrying what was said, what was looked up, what
was decided and how long each part took. Logs are read when something has gone
wrong on a real call, so they have to carry enough to reconstruct it.

They must not carry enough to be a leak. Passcodes never appear, phone numbers
and addresses are reduced to something recognisable but not usable, and the
redaction runs over the record on its way out rather than being remembered at
each call site.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# A four digit run that sits next to the word for it. Passcodes are never given
# to the model, so this should never match; it runs anyway because a spoken
# credential cannot be taken back and the check is nearly free.
# The gap allows for the words people put between the label and the number:
# "the passcode is 8241", "pin was 8 2 4 1". Matching only punctuation there
# misses every natural phrasing.
PASSCODE_NEARBY = re.compile(
    r"(passcode|pass ?code|\bpin\b|\bcode\b)([^\d]{0,20})(\d[\d\s\-]{2,})", re.I
)

PHONE = re.compile(r"\+?\d[\d\s\-().]{8,}\d")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Field names whose contents never belong in a log line whatever they hold.
NEVER_LOGGED = {
    "passcode", "spoken", "verbal_passcode", "verbal_passcode__c",
    "client_secret", "api_key", "secret_key", "authorization", "password",
}


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    # Enough to tell two callers apart in a log, not enough to ring either.
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def redact(value: Any) -> Any:
    """Strip anything that should not survive into a log line."""
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if key.lower() in NEVER_LOGGED else redact(inner))
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    text = PASSCODE_NEARBY.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", value)
    text = EMAIL.sub("[email]", text)
    return PHONE.sub(_mask_phone, text)


class JsonFormatter(logging.Formatter):
    """One line of JSON per record, with the sensitive parts removed."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def configure(level: str | None = None, as_json: bool | None = None) -> None:
    """Set up logging once, at startup.

    Plain text while developing, because a person is reading it. JSON once
    deployed, because a machine is.
    """
    import os

    level = level or os.getenv("LOG_LEVEL", "INFO")
    if as_json is None:
        as_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if as_json
        else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Noisy libraries that would otherwise bury the call records.
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_turn(logger: logging.Logger, call_id: str, **fields: Any) -> None:
    """Write the one record that describes a turn."""
    logger.info(
        "turn", extra={"fields": redact({"call_id": call_id, **fields})}
    )
