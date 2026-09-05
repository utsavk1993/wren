# Wren

A home security troubleshooting voice agent. You talk to it in the browser; it
identifies you, diagnoses your device problem against a real knowledge base,
walks you through the fix one step at a time, and hands off to a human when it
cannot help.

Built as a portfolio project modeled on Cresta's Brinks Home deployment.

## Architecture

A streaming cascade — every stage streams into the next, so the caller never
waits for a complete sentence upstream.

```
Browser mic
  -> WebRTC audio in
    -> Voice activity detection + turn detection
      -> Speech-to-Text  (Deepgram, streaming)
        -> Orchestrator  (Claude + retrieval + tools + guardrails)
          -> Text-to-Speech  (Cartesia, streaming)
            -> WebRTC audio out
              -> Browser speaker
```

**Speech-to-Text (STT)** transcribes what the caller says. The **orchestrator**
decides how to respond, retrieving troubleshooting steps from the knowledge base
and calling backend tools for account and device data. **Text-to-Speech (TTS)**
turns the reply back into audio.

Each stage streams into the next instead of waiting for it to finish, so speech
synthesis starts on the first complete sentence while the model is still
writing. Run end to end the three latencies would stack and leave audible gaps
in the conversation.

This is a cascade rather than a single speech-to-speech model. A speech-to-speech
model is faster and sounds more natural, but it gives up reliable tool calling
and leaves no transcript to audit — both of which a support agent needs.

### Services

| Service | What it is |
|---|---|
| `agent` | Pipecat voice orchestrator — turn taking, retrieval, tools, guardrails |
| `api`   | FastAPI mock backend — customers, devices, tickets, escalation |
| `db`    | Postgres + pgvector — knowledge base vectors and mock CRM data |
| `web`   | React + TypeScript client — mic, call controls, live transcript |

There is no phone number in v1. Clicking **Call** in the browser is the entry
point and the agent greets first, mirroring an answered inbound call. Telephony
is a later migration; the transport sits behind an interface so the swap is a
config change.

## Prerequisites

- Docker and Docker Compose
- API keys for Deepgram (STT), Anthropic (LLM), Cartesia (TTS), and an
  embeddings provider

## Running

```sh
cp .env.example .env   # then fill in your keys
docker compose up
```

Then:

- Web client — http://localhost:5174
- API — http://localhost:8001/health
- Agent — http://localhost:7860/health

Host ports default to 5174/8001/5433 rather than the usual 5173/8000/5432 so
Wren can run alongside other local stacks. Override them in `.env`.

## Build status

Progress is tracked in [the issues](https://github.com/utsavk1993/wren/issues).

- [x] **Phase 0** — skeleton, compose, health checks
- [ ] **Phase 1** — mock backend and tools
- [ ] **Phase 2** — knowledge base and retrieval
- [ ] **Phase 3** — text orchestrator
- [ ] **Phase 4** — voice pipeline
- [ ] **Phase 5** — web client
- [ ] **Phase 6** — evaluation and observability
- [ ] **Phase 7** — deployment
