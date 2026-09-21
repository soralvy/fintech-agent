-- Milestone 1 schema: documents and their embedded chunks.
--
-- This file is the migration artifact (docs/DECISIONS.md section 5.1); there is
-- no migration framework. Apply it explicitly during setup and in tests. Every
-- statement is idempotent so re-applying it to an existing database is safe.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY,
    filename text NOT NULL,
    content_type text NOT NULL,
    sha256 char(64) NOT NULL UNIQUE,
    page_count integer,
    chunk_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_chunk_count_non_negative
        CHECK (chunk_count >= 0),
    CONSTRAINT documents_page_count_positive
        CHECK (page_count IS NULL OR page_count >= 1)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    page_number integer,
    content text NOT NULL,
    token_count integer,
    embedding vector(1536) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_document_id_chunk_index_key
        UNIQUE (document_id, chunk_index),
    CONSTRAINT document_chunks_content_not_blank
        CHECK (length(trim(content)) > 0),
    CONSTRAINT document_chunks_chunk_index_non_negative
        CHECK (chunk_index >= 0),
    CONSTRAINT document_chunks_page_number_positive
        CHECK (page_number IS NULL OR page_number >= 1)
);

-- Deliberately no ANN (HNSW/IVFFlat) index: docs/SPEC.md section 8.2 chooses
-- exact search for the intentionally small demo corpus.
CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx
    ON document_chunks (document_id);
