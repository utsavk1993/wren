-- Wren database bootstrap. Runs once, on first boot of an empty postgres volume.
-- Enables the vector extension that the knowledge base embeddings depend on.

CREATE EXTENSION IF NOT EXISTS vector;
