-- T-028, T-029, T-030, T-031
-- Initial schema for Provena PostgreSQL database

-- Enable pgvector extension for similarity search
CREATE EXTENSION IF NOT EXISTS vector;
-- Enable UUID support
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── API Keys ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash                VARCHAR(64) UNIQUE NOT NULL,  -- SHA-256 of plaintext key
    org_name                VARCHAR(256),
    tier                    VARCHAR(32) DEFAULT 'free',
    is_active               BOOLEAN DEFAULT TRUE,
    daily_register_limit    INTEGER DEFAULT 100,
    daily_verify_limit      INTEGER DEFAULT 500,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at            TIMESTAMPTZ
);

-- ─── C2PA Manifests ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS manifests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    c2pa_json       JSONB NOT NULL,
    signature       BYTEA,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Provenance Records (append-only) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS provenance_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Perceptual hash stored as 64-bit string for Hamming distance queries
    image_phash     BIT(64) NOT NULL,
    -- Also store as integer for SQLite cross-compat and human readability
    image_phash_int BIGINT  NOT NULL,
    image_phash_hex VARCHAR(16) NOT NULL,

    model_id        VARCHAR(128) NOT NULL,
    creator_did     VARCHAR(512),
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Hex-encoded 256-bit neural watermark payload
    payload_hex     VARCHAR(64) NOT NULL,

    -- Foreign keys
    manifest_id     UUID REFERENCES manifests(id),
    api_key_id      UUID REFERENCES api_keys(id),

    -- Optional extra metadata
    custom_fields   JSONB DEFAULT '{}',

    -- Dedup window: same model + phash within 1 second
    UNIQUE (model_id, image_phash_hex, registered_at)
);

-- T-031: IVFFlat index on image_phash for approximate nearest-neighbour Hamming search
CREATE INDEX IF NOT EXISTS idx_prov_phash_ivfflat
    ON provenance_records
    USING ivfflat (image_phash bit_hamming_ops)
    WITH (lists = 100);

-- Standard B-Tree indices for direct lookups
CREATE INDEX IF NOT EXISTS idx_prov_model_id   ON provenance_records(model_id);
CREATE INDEX IF NOT EXISTS idx_prov_api_key_id ON provenance_records(api_key_id);
CREATE INDEX IF NOT EXISTS idx_prov_manifest   ON provenance_records(manifest_id);
