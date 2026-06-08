-- sql/schema.sql — one database holds everything.
-- Apply once after provisioning Postgres:
--   psql $DATABASE_URL -f sql/schema.sql

-- pgvector for style retrieval
CREATE EXTENSION IF NOT EXISTS vector;

-- Your past sent emails, embedded, so drafts can match your voice
CREATE TABLE IF NOT EXISTS style_corpus (
    id          bigserial PRIMARY KEY,
    text        text NOT NULL,            -- a past sent message (or excerpt)
    embedding   vector(1536),             -- text-embedding-3-small dim
    created_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS style_corpus_embedding_idx
    ON style_corpus USING ivfflat (embedding vector_cosine_ops);

-- Synced mail threads (lightweight; full body fetched on demand)
CREATE TABLE IF NOT EXISTS threads (
    id          text PRIMARY KEY,         -- Gmail thread id
    subject     text,
    last_from   text,
    snippet     text,
    unread      boolean DEFAULT true,
    metadata    jsonb,                    -- labels, participants, etc. (flexible)
    synced_at   timestamptz DEFAULT now()
);

-- The approval queue: every consequential action the agent proposes
CREATE TABLE IF NOT EXISTS pending_actions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        text NOT NULL,            -- 'send_email' | 'create_event'
    payload     jsonb NOT NULL,           -- recipient, subject, body / event fields
    status      text NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|executed
    created_at  timestamptz DEFAULT now(),
    decided_at  timestamptz
);

-- Conversation history (schemaless corner in JSONB — no Mongo needed)
CREATE TABLE IF NOT EXISTS messages (
    id              bigserial PRIMARY KEY,
    conversation_id uuid NOT NULL,
    role            text NOT NULL,        -- user|assistant|tool
    content         text,
    metadata        jsonb,                -- tool_calls, usage, citations
    created_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conv_idx ON messages (conversation_id, created_at);

-- Encrypted OAuth tokens (single-user v1)
CREATE TABLE IF NOT EXISTS google_tokens (
    user_id     text PRIMARY KEY,
    token_enc   bytea NOT NULL,           -- Fernet-encrypted token JSON
    updated_at  timestamptz DEFAULT now()
);
