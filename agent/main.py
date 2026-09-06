"""Wren voice orchestrator.

Owns the conversation: turn taking, knowledge base retrieval, tool calls,
guardrails, and the audio pipeline that connects them.

A single conversational turn moves through three stages, which is why this
design is called a "cascade":

    1. Speech-to-Text (STT) transcribes the caller's audio into text.
    2. A Large Language Model (LLM) reads that text, decides what to say, looks
       up troubleshooting steps in the knowledge base, and calls backend tools
       when it needs account or device data.
    3. Text-to-Speech (TTS) turns the model's written reply back into audio the
       caller hears.

Every stage streams into the next rather than waiting for the one before it to
finish. Transcription emits partial results while the caller is still speaking,
the model emits tokens as it writes, and speech synthesis begins on the first
complete sentence instead of the finished reply. Running the stages end to end
would stack their latencies and leave multi-second gaps in the conversation;
overlapping them is what keeps a reply under roughly a second.

The costly stage is deciding that the caller has actually stopped talking, which
takes longer than transcription does. Ending a turn too eagerly cuts the caller
off mid-thought; waiting too long makes the agent feel slow.

The alternative architecture feeds audio straight into a single model that emits
audio back, with no text in between. That is faster and sounds more natural, but
it gives up reliable tool calling and leaves no transcript to audit. A support
agent has to look up accounts and open tickets, and its calls have to be
reviewable, so the cascade is the better trade here.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import observability
import history
from call import CallHandler, capabilities, warm_up

observability.configure()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ready = capabilities()
    # Pay the cold costs now rather than making the first caller wait through
    # them: a token from the customer system, and the embedding model loading.
    await warm_up()
    log.info("agent ready: %s", {**ready, "tracing": observability.tracer().enabled})
    yield


app = FastAPI(title="Wren Agent", version="0.1.0", lifespan=lifespan)

# The browser client is served from a different port in development and a
# different host once deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("WREN_ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent"}


@app.get("/capabilities")
def read_capabilities() -> dict[str, object]:
    """What this deployment can actually do.

    The client needs to know whether there is speech at the other end before it
    asks for a microphone, so that a deployment without speech credentials
    offers typing instead of failing silently.
    """
    return capabilities()


@app.get("/calls")
def list_calls(limit: int = 50) -> list[dict]:
    """Recent calls, newest first."""
    return [dict(row) for row in history.list_calls(limit=limit)]


@app.get("/calls/{call_id}")
def read_call(call_id: str) -> dict:
    """One call in full: every turn, every tool call, every timing."""
    found = history.get_call(call_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such call")
    return dict(found)


@app.websocket("/call")
async def call(websocket: WebSocket) -> None:
    """One call, for as long as the caller stays connected."""
    await websocket.accept()
    handler = CallHandler(websocket)
    try:
        await handler.run()
    except WebSocketDisconnect:
        log.info("caller hung up: %s", handler.call_id)
    except Exception:
        log.exception("call %s failed", handler.call_id)
    finally:
        await handler.aclose()
