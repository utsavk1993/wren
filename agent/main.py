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

from fastapi import FastAPI

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Wren Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent"}
