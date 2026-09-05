"""Wren mock backend: customer, device, ticket, and escalation systems.

Stands in for the CRM and device telemetry systems a real deployment would
integrate with. The agent reaches these through tool calls, never directly.
"""

import logging
import os

from fastapi import FastAPI

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Wren API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}
