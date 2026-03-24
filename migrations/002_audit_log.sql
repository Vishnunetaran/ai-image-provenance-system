-- T-032: Append-only verification audit log (required for legal discovery)

CREATE TABLE IF NOT EXISTS verification_log (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id        UUID REFERENCES provenance_records(id),
    api_key_id       UUID REFERENCES api_keys(id),
    queried_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hamming_distance INTEGER,
    status_returned  VARCHAR(32) NOT NULL,  -- VERIFIED | VERIFIED_MODIFIED | UNREGISTERED | TAMPERED
    confidence       FLOAT,
    request_id       VARCHAR(64)           -- X-Request-ID header value
);

-- Audit log is append-only: no updates or deletes should be issued programmatically.
-- This is enforced at the application layer (never issue UPDATE/DELETE on this table).
CREATE INDEX IF NOT EXISTS idx_vlog_api_key    ON verification_log(api_key_id);
CREATE INDEX IF NOT EXISTS idx_vlog_record_id  ON verification_log(record_id);
CREATE INDEX IF NOT EXISTS idx_vlog_queried_at ON verification_log(queried_at);
