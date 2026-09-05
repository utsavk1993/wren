# Wren

A home security troubleshooting voice agent. You talk to it, it works out who
you are, checks your equipment, walks you through a fix one step at a time, and
hands you to a person when it cannot help.

Built as a portfolio project modeled on Cresta's Brinks Home deployment.

**[How it works](docs/architecture.md)** walks through a whole call with
diagrams, written for someone who has not read the code.

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
and calling out to the customer and equipment systems. **Text-to-Speech (TTS)**
turns the reply back into audio.

Each stage streams into the next instead of waiting for it to finish, so speech
synthesis starts on the first complete sentence while the model is still
writing. Run end to end the three latencies would stack and leave audible gaps
in the conversation.

This is a cascade rather than a single speech-to-speech model. A speech-to-speech
model is faster and sounds more natural, but it gives up reliable tool calling
and leaves no transcript to audit — both of which a support agent needs.

## Where the data lives

The agent owns very little. Customer records and equipment state belong to
systems it integrates with, exactly as they would in a real deployment.

| System | Holds |
|---|---|
| Salesforce | Households, the people who call, service plan, account status, verbal passcode, support history |
| Supabase | Installed equipment and its live state |
| Postgres | Call transcripts and the embedded knowledge base — the agent's own record |

Copying customer or device data locally would mean answering from a stale copy
about whether a sensor is currently reporting, which is the one thing this agent
cannot be wrong about.

## What it refuses to do

- Say anything about an account before the caller has given the passcode
- Continue after two failed attempts, rather than offering a third guess
- Troubleshoot equipment on an account nobody is monitoring, because a caller
  who fixes a sensor there believes they are protected when they are not
- Repeat a repair on equipment that has already failed it several times
- Give any instruction that did not come from the knowledge base
- Answer anything about billing, contracts, or cancelling
- Promise a time, a cost, or a visit

Each of these is stated in the agent's instructions and enforced separately in
code, because a model can be argued out of an instruction.

## Running it

```sh
cp .env.example .env   # then fill in your keys
docker compose up
```

- Client — http://localhost:5174
- Agent — http://localhost:7860/health
- API — http://localhost:8001/health

Host ports default to 5174/8001/5433 rather than the usual 5173/8000/5432 so
Wren can run alongside other local stacks. Override them in `.env`.

Only `ANTHROPIC_API_KEY` and the Salesforce and Supabase credentials are needed
to hold a conversation; the client falls back to typing when speech credentials
are absent, and says so rather than showing a dead microphone button.

### Filling the connected systems

```sh
cd seed && set -a && . ../.env && set +a
python load_salesforce.py    # households, people, support history
python load_supabase.py      # equipment and its state
```

Both match on an identifier this project owns, so re-running updates records in
place rather than creating duplicates.

### Building the knowledge base

```sh
docker compose exec agent python -m rag.ingest
```

Embeddings run in-process by default, so this needs no API key.

## Checking it works

```sh
docker compose exec agent python repl.py --show-tools   # hold a call by typing
docker compose exec agent python -m rag.retrieve "my door sensor is offline"
docker compose exec agent python -m rag.evaluate        # retrieval quality
docker compose exec agent python -m eval.run -n 3       # whole scored calls
```

## Deploying

See [deploy/README.md](deploy/README.md).

## Where the time goes

Measured against the live systems rather than estimated:

| Stage | Budget | Measured |
|---|---|---|
| Model, first token | 500 ms | 590–880 ms per round |
| Customer and equipment lookups | 250 ms | 300–600 ms, run together |
| Retrieval | 150 ms | under 150 ms |

The dominant cost is not any single stage. A turn needing two or three tool
calls costs that many model round trips, and the caller waits through all of
them, which is why an acknowledgement goes out while the work happens.
