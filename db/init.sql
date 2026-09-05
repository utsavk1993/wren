-- Wren database bootstrap. Runs once, on first boot of an empty postgres volume,
-- and is reapplied by the API on every start so an existing database still picks
-- up changes.
--
-- This holds only what the agent itself produces. Customer records live in the
-- customer system and equipment state lives in the telemetry platform; copying
-- either one here would mean answering from a stale duplicate about whether a
-- sensor is online, which is the one thing this agent cannot be wrong about.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Knowledge base
--
-- Troubleshooting articles split into chunks and stored with their embedding,
-- so a caller's description of a problem can be matched by meaning rather than
-- by keyword.
-- ---------------------------------------------------------------------------

-- 1536 dimensions matches text-embedding-3-small. Changing embedding model
-- means changing this width and re-ingesting every article.
CREATE TABLE IF NOT EXISTS kb_chunks (
    id            BIGSERIAL PRIMARY KEY,
    article_slug  TEXT NOT NULL,
    article_title TEXT NOT NULL,
    device_type   TEXT,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    embedding     VECTOR(1536),
    UNIQUE (article_slug, chunk_index)
);

-- Cosine distance, because embedding similarity is about direction rather than
-- magnitude. Harmless while the table is empty.
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Call records
--
-- One row per conversation, kept for quality review and for scoring the agent
-- against test conversations.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS calls (
    id                   TEXT PRIMARY KEY,
    customer_external_id TEXT,
    problem_category     TEXT,
    steps_tried          JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome              TEXT,
    escalated            BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason    TEXT,
    transcript           JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS calls_customer_idx ON calls (customer_external_id);
