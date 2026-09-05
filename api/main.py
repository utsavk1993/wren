"""Wren backend.

Serves the agent's own data and health. Customer records and device telemetry
are not served from here: those live in the connected systems and are reached
through their own clients.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import wait_for_database
from schema import apply_schema

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring the database to a known state before serving."""
    wait_for_database()
    apply_schema()
    yield


app = FastAPI(title="Wren API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}
