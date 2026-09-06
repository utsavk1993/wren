"""Sending traces somewhere they can be looked at, when that is configured.

Optional on purpose. Nothing here should be able to slow down or break a call:
if the tracing service is not configured, or is down, the conversation carries
on exactly as it would have.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


class Tracer:
    """Records calls and turns, or does nothing at all."""

    def __init__(self) -> None:
        self._client = None
        public, secret = (
            os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            os.getenv("LANGFUSE_SECRET_KEY", ""),
        )
        if not (public and secret):
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=public,
                secret_key=secret,
                host=os.getenv("LANGFUSE_HOST") or None,
            )
            log.info("tracing enabled")
        except Exception as exc:  # noqa: BLE001 - never let this stop a call
            log.warning("tracing unavailable, carrying on without it: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def turn(self, call_id: str, said: str, replied: str, **fields: Any) -> None:
        if self._client is None:
            return
        from observability.logging import redact

        try:
            self._client.trace(
                name="turn",
                session_id=call_id,
                input=redact(said),
                output=redact(replied),
                metadata=redact(fields),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record a trace: %s", exc)


_tracer: Tracer | None = None


def tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
