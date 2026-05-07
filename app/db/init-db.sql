-- ─────────────────────────────────────────────────────────────────────────────
-- Intelligent Document Explorer — Full DB Init (Production Ready, Idempotent)
-- ─────────────────────────────────────────────────────────────────────────────

-- ========================
-- Extensions
-- ========================
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ========================
-- DOCUMENTS TABLE
-- ========================
CREATE TABLE IF NOT EXISTS documents (
    document_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    file_name         TEXT NOT NULL,
    file_type         TEXT DEFAULT 'unsupported',

    blob_path         TEXT NOT NULL,
    storage_container TEXT NOT NULL,

    uploaded_at       TIMESTAMPTZ DEFAULT NOW(),

    status            TEXT NOT NULL CHECK (
                          status IN ('pending', 'processing', 'completed', 'failed')
                      ) DEFAULT 'pending',

    page_count        INT DEFAULT 0,

    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ========================
-- PAGES TABLE
-- ========================
CREATE TABLE IF NOT EXISTS pages (
    id              SERIAL PRIMARY KEY,
    document_id     UUID NOT NULL,
    page_number     INT NOT NULL,
    page_blob_path  TEXT,
    local_path      TEXT,

    CONSTRAINT fk_pages_document
        FOREIGN KEY (document_id)
        REFERENCES documents(document_id)
        ON DELETE CASCADE,

    -- ✅ Prevent duplicate pages
    CONSTRAINT unique_page_per_doc UNIQUE (document_id, page_number)
);

-- ========================
-- OCR RESULTS TABLE
-- ========================
CREATE TABLE IF NOT EXISTS ocr_results (
    id          SERIAL PRIMARY KEY,
    document_id UUID NOT NULL,
    page_number INT NOT NULL,
    content     TEXT,
    tags        TEXT[],
    bbox        JSONB,
    line_index  INT,
    created_at  TIMESTAMP DEFAULT NOW(),

    CONSTRAINT fk_ocr_document
        FOREIGN KEY (document_id)
        REFERENCES documents(document_id)
        ON DELETE CASCADE,

    -- ✅ Prevent duplicate OCR per page
    CONSTRAINT unique_ocr_per_page UNIQUE (document_id, page_number)
);

-- ========================
-- EMBEDDINGS TABLE
-- ========================
CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,

    document_id UUID NOT NULL,
    file_name TEXT,
    file_type TEXT,

    page_number INT NOT NULL,
    chunk_id INT NOT NULL,

    content TEXT,

    -- BM25 column
    content_tsv tsvector,

    -- Vector embedding
    embedding VECTOR(1024),

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT fk_embeddings_document
        FOREIGN KEY (document_id)
        REFERENCES documents(document_id)
        ON DELETE CASCADE,

    -- ✅ Prevent duplicate chunks
    CONSTRAINT unique_chunk_per_page UNIQUE (document_id, page_number, chunk_id)
);

-- ========================
-- BM25 TRIGGER FUNCTION
-- ========================
CREATE OR REPLACE FUNCTION update_embeddings_tsv()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ========================
-- BM25 TRIGGER
-- ========================
DROP TRIGGER IF EXISTS trg_update_embeddings_tsv ON embeddings;

CREATE TRIGGER trg_update_embeddings_tsv
BEFORE INSERT OR UPDATE ON embeddings
FOR EACH ROW
EXECUTE FUNCTION update_embeddings_tsv();

-- ========================
-- INDEXES
-- ========================

-- Documents
CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents (status);

-- Pages
CREATE INDEX IF NOT EXISTS idx_pages_document_id
    ON pages (document_id);

-- OCR
CREATE INDEX IF NOT EXISTS idx_ocr_document_id
    ON ocr_results (document_id);

-- Embeddings metadata
CREATE INDEX IF NOT EXISTS idx_embeddings_document_id
    ON embeddings (document_id);

-- ✅ Composite index for better filtering
CREATE INDEX IF NOT EXISTS idx_embeddings_doc_page
    ON embeddings (document_id, page_number);

-- ========================
-- BM25 INDEX (GIN)
-- ========================
CREATE INDEX IF NOT EXISTS idx_embeddings_tsv
ON embeddings USING GIN(content_tsv);

-- ========================
-- VECTOR INDEX (HNSW)
-- ========================
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
ON embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (
    m = 16,
    ef_construction = 64
);

-- ========================
-- ANALYZE (IMPORTANT)
-- ========================
ANALYZE;

-- ========================
-- CONVERSATIONS TABLE
-- ========================

CREATE TABLE IF NOT EXISTS conversations (
    id BIGSERIAL PRIMARY KEY,

    conversation_id TEXT NOT NULL,
    turn_index INT NOT NULL,

    role TEXT NOT NULL CHECK (
        role IN ('user', 'assistant', 'system')
    ),

    content TEXT NOT NULL,

    embedding VECTOR(1024),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ========================
-- INDEXES
-- ========================

CREATE INDEX IF NOT EXISTS idx_conversations_conversation_id
ON conversations(conversation_id);

CREATE INDEX IF NOT EXISTS idx_conversations_created_at
ON conversations(created_at);

CREATE INDEX IF NOT EXISTS idx_conversations_turn
ON conversations(conversation_id, turn_index);

-- Vector search index
CREATE INDEX IF NOT EXISTS idx_conversations_embedding_hnsw
ON conversations
USING hnsw (embedding vector_cosine_ops)
WITH (
    m = 16,
    ef_construction = 64
);

ANALYZE conversations;