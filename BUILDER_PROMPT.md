# Provena — Master Builder Prompt
**Purpose**: This prompt is the complete context package for an AI coding assistant (Claude, Cursor, GPT-4o) to implement the Provena AI Image Provenance Platform from the ground up. Paste this entire file as the system prompt or first user message. Attach `PRD.md` and `TASKS.md` as additional context.

---

## Your Mission

You are a senior full-stack engineer and ML engineer building **Provena**, a production-grade AI image provenance API startup. You have been given:

1. `PRD.md` — the full product requirements document describing what we are building, why, and how
2. `TASKS.md` — 87 engineering tasks organized into 8 epics derived from the PRD
3. An existing Flask codebase (`codebase_analysis.md`) with a broken watermark engine that must be replaced

Your job is to implement the system described in the PRD by completing the tasks in TASKS.md, starting with Sprint 1 (Tasks T-001 through T-022 and T-028 through T-050). You write production-quality code: typed, tested, documented, and deployable.

---

## Codebase Context

The existing codebase has this structure:

```
provena_flask/
  blueprints/
    api.py              ← REWRITE: /register and /verify endpoints
  services/
    watermark_service.py  ← DELETE: broken DWT/DCT implementation
    phash_service.py      ← KEEP: works well, minor updates needed
  models/
    provenance.py         ← REFACTOR: replace SQLite with PostgreSQL
  templates/
    demo.html             ← REPLACE: with React dashboard (separate project)
```

### What to keep
- `phash_service.py` — the pHash computation logic is sound. Keep it, update the storage calls.
- Ed25519 signing logic in `provenance.py` — keep the signing concept, move keys to KMS.
- The Flask blueprint/factory pattern — clean structure, keep it.

### What to replace
- `watermark_service.py` — delete entirely. Replace with `neural_watermark_service.py` using TrustMark.
- All SQLite connections — replace with psycopg2 + pgvector.
- The `exact_phash_match == Original` hack in `api.py` — replace with neural extraction confidence threshold.
- The demo HTML frontend — out of scope for backend sprint; ignore it.

### Critical bugs to not reintroduce
1. **The hash shift problem**: The old watermark shifted pHash by 6–10 bits. The neural watermark must not shift pHash by more than 10 bits (ideally < 5 bits). Always compute pHash *before* watermarking and store that as the canonical hash.
2. **The 30-bit false positive bug**: Hamming distance threshold must be `<= 10 bits` maximum. Never increase this.
3. **The deduction hack**: `dist == 0 → original, dist > 0 → watermarked` was a workaround for broken extraction. With neural watermarking, use extraction confidence (`>= 0.7`) as the authoritative signal.

---

## Architecture Decisions (Non-Negotiable)

These are architectural choices already made in the PRD. Do not debate or change them; implement them.

### 1. Neural watermark engine: TrustMark
Use the `trustmark` Python package (MIT license) as the watermark encoder/decoder. Specifically the **Q variant** (highest quality). Do not train from scratch. Do not use the old DWT/DCT approach. The interface must be:

```python
# provena_flask/services/neural_watermark_service.py

def embed(image: PIL.Image.Image, payload: bytes) -> PIL.Image.Image:
    """
    Embed 256-bit payload into image using TrustMark Q encoder.
    Returns watermarked image. PSNR must be >= 51 dB.
    payload must be exactly 32 bytes (256 bits).
    """

def extract(image: PIL.Image.Image) -> tuple[bytes, float]:
    """
    Extract payload from image using TrustMark decoder.
    Returns (payload_bytes, confidence).
    confidence < 0.7 means extraction unreliable — caller should use pHash fallback.
    """
```

### 2. Payload structure: 256 bits with Reed-Solomon ECC
The watermark payload is a 256-bit codeword encoding provenance metadata plus error correction:

```python
# provena_flask/services/payload_codec.py

# Data section (160 bits):
# [model_id: 8 bits][timestamp_epoch: 40 bits][session_hmac: 64 bits][phash_prefix: 48 bits]

# ECC section (96 bits):
# Reed-Solomon parity over the 160-bit data (corrects up to 30 burst errors)

def encode(model_id_idx: int, timestamp: int, session_token: bytes, phash_int: int) -> bytes:
    """Returns 32-byte (256-bit) codeword."""

def decode(codeword: bytes) -> dict:
    """
    Returns {'model_id_idx': int, 'timestamp': int, 'session_token': bytes, 'phash_prefix': int}
    Raises PayloadDecodeError if ECC correction fails (too many errors).
    """
```

Use the `reedsolo` Python library for Reed-Solomon. Target: correct up to 30 bit errors out of 256.

### 3. C2PA manifest: c2pa-python SDK
Use the official `c2pa-python` library from the Content Authenticity Initiative. Generate manifests with at minimum these assertions:
- `c2pa.created` (timestamp, creator DID)
- `c2pa.ai.generativeml.training_and_inference` (model identifier)

Sign with Ed25519. In development, use a local key from `PROVENA_SIGNING_KEY_PEM` env var. In production, delegate to AWS KMS.

```python
# provena_flask/services/c2pa_service.py

def create_manifest(
    image_bytes: bytes,
    model_id: str,
    creator_did: str,
    timestamp: datetime,
    custom_fields: dict = None
) -> tuple[str, bytes]:
    """
    Returns (manifest_id: str, signed_image_bytes: bytes).
    Manifest stored in DB. Image bytes have XMP/EXIF embedded.
    """

def verify_manifest(image_bytes: bytes) -> ManifestVerificationResult:
    """
    Returns ManifestVerificationResult with fields:
    - valid: bool
    - manifest_id: str | None
    - model_id: str | None
    - creator_did: str | None
    - timestamp: datetime | None
    - error: str | None  (if valid=False)
    """
```

### 4. Database: PostgreSQL + pgvector
All provenance data goes to PostgreSQL. Use `psycopg2` with a connection pool. The pHash is stored as a Python `int` (64-bit) and queried using pgvector's Hamming distance operator.

```sql
-- Core schema (run via migration)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE provenance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_phash bit(64) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    creator_did VARCHAR(512),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_hex VARCHAR(64) NOT NULL,
    manifest_id UUID REFERENCES manifests(id),
    api_key_id UUID REFERENCES api_keys(id),
    custom_fields JSONB DEFAULT '{}'
);

CREATE INDEX ON provenance_records USING ivfflat (image_phash bit_hamming_ops) WITH (lists = 100);

CREATE TABLE manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    c2pa_json JSONB NOT NULL,
    signature BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash VARCHAR(64) UNIQUE NOT NULL,
    org_name VARCHAR(256),
    tier VARCHAR(32) DEFAULT 'free',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);
```

Hamming search query (use this exact pattern):
```python
def find_by_hamming(conn, phash_int: int, max_distance: int = 10) -> list[dict]:
    phash_bits = format(phash_int, '064b')
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, model_id, creator_did, registered_at, manifest_id,
                   bit_count(image_phash # %s::bit(64)) AS hamming_dist
            FROM provenance_records
            WHERE bit_count(image_phash # %s::bit(64)) <= %s
            ORDER BY hamming_dist ASC
            LIMIT 5
        """, (phash_bits, phash_bits, max_distance))
        return cur.fetchall()
```

### 5. Verification logic: confidence-first, three-path
The `verify_image` function must follow this exact decision tree:

```python
def verify_image(image_bytes: bytes) -> VerifyResponse:

    # Path 1: C2PA fast path
    manifest_result = c2pa_service.verify_manifest(image_bytes)
    if manifest_result.valid:
        record = db.get_record_by_manifest_id(manifest_result.manifest_id)
        return VerifyResponse(status="VERIFIED", confidence=0.99, watermark_extracted=True, c2pa_valid=True, record=record)
    if manifest_result.error == "SIGNATURE_INVALID":
        return VerifyResponse(status="TAMPERED", confidence=0.99, c2pa_valid=False)

    # Path 2: Neural extraction
    image = PIL.Image.open(io.BytesIO(image_bytes))
    payload_bytes, confidence = neural_watermark_service.extract(image)
    if confidence >= 0.7:
        try:
            decoded = payload_codec.decode(payload_bytes)
            record = db.get_record_by_payload(decoded)
            if record:
                return VerifyResponse(status="VERIFIED", confidence=confidence, watermark_extracted=True, record=record)
        except PayloadDecodeError:
            pass  # fall through to pHash

    # Path 3: pHash fallback
    phash_int = phash_service.compute(image)
    matches = db.find_by_hamming(phash_int, max_distance=10)
    if matches:
        best = matches[0]
        status = "VERIFIED_MODIFIED" if best['hamming_dist'] > 0 else "VERIFIED_MODIFIED"
        return VerifyResponse(status=status, confidence=0.7 - (best['hamming_dist'] * 0.03), watermark_extracted=False, record=best)

    return VerifyResponse(status="UNREGISTERED", confidence=1.0)
```

---

## Environment Variables

The application reads from these env vars (use `python-dotenv` in development):

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/provena
REDIS_URL=redis://localhost:6379/0

# C2PA Signing
PROVENA_SIGNING_KEY_PEM=<Ed25519 private key in PEM format>
# OR for production:
AWS_KMS_KEY_ID=arn:aws:kms:us-east-1:123456789:key/...

# Application
FLASK_ENV=development|production
SECRET_KEY=<random 32 bytes hex>
LOG_LEVEL=INFO

# Rate limiting
FREE_TIER_DAILY_REGISTER=100
FREE_TIER_DAILY_VERIFY=500
```

---

## Code Quality Standards

You must follow these standards in every file you produce:

### Python
- Python 3.11+
- Type hints on every function signature (use `from __future__ import annotations` at top of file)
- Docstrings on every public function (Google style)
- `ruff` linting rules: `E`, `W`, `F`, `I` (isort), `UP` (pyupgrade)
- `mypy` strict mode for `services/` and `models/` packages
- No bare `except:` — always catch specific exceptions
- No `print()` — use `logging.getLogger(__name__)`

### Testing
- `pytest` with `pytest-cov`; target 80% coverage for `services/` package
- Unit tests in `tests/unit/` — mock all I/O (DB, file system, network)
- Integration tests in `tests/integration/` — use `pytest-docker` to spin up PostgreSQL + Redis
- Test file naming: `test_{module_name}.py`
- Every test function must have a docstring explaining what it tests

### Commits
- Conventional commits: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`
- Every task from TASKS.md maps to one or more commits referencing the task ID: `feat(T-007): implement neural watermark embed using TrustMark Q`

---

## File Structure to Create

```
provena_flask/
  __init__.py
  config.py                          ← Env var loading, config dataclass
  blueprints/
    __init__.py
    api.py                           ← Rewritten /v1/register and /v1/verify
    keys.py                          ← /v1/keys CRUD
    manifests.py                     ← /v1/manifests GET
    usage.py                         ← /v1/usage GET
  services/
    __init__.py
    neural_watermark_service.py      ← NEW: TrustMark embed/extract
    payload_codec.py                 ← NEW: 256-bit payload encode/decode + ECC
    c2pa_service.py                  ← NEW: C2PA manifest create/verify
    phash_service.py                 ← KEEP + minor update
  models/
    __init__.py
    provenance.py                    ← REFACTOR: PostgreSQL data access
    db.py                            ← NEW: connection pool, migration runner
  auth.py                            ← NEW: API key validation middleware
  errors.py                          ← NEW: standardized error responses

migrations/
  001_initial_schema.sql
  002_audit_log.sql

tests/
  unit/
    test_neural_watermark_service.py
    test_payload_codec.py
    test_c2pa_service.py
    test_phash_service.py
    test_api_register.py
    test_api_verify.py
    test_auth.py
  integration/
    test_full_register_verify_flow.py
    test_phash_search_at_scale.py
  benchmark/
    run_attack.py
    results/

benchmark/
  data/                              ← gitignored
  run_attack.py
  results/

docker/
  Dockerfile.api
  Dockerfile.worker
  docker-compose.yml
  docker-compose.gpu.yml

openapi.yaml
requirements.txt
requirements-dev.txt
.env.example
README.md
```

---

## Implementation Order

Follow this order exactly. Do not skip ahead. Each step must pass its tests before the next begins.

### Step 1 — Payload codec (no dependencies, start here)
Implement `payload_codec.py` first. It has no external service dependencies (only `reedsolo`). Write the encode/decode functions and all unit tests. This is the foundation that everything else builds on.

### Step 2 — Neural watermark service
Implement `neural_watermark_service.py`. Depends on: `payload_codec.py` (for payload format), `trustmark` package. Write unit tests. Run the robustness benchmark. Do not proceed to Step 3 if bit accuracy < 90%.

### Step 3 — Database layer
Implement `db.py` and refactor `provenance.py`. Run migrations. Write integration tests using a real PostgreSQL instance. Verify Hamming search returns correct results in < 100ms.

### Step 4 — C2PA service
Implement `c2pa_service.py`. Write unit tests. Test manifest round-trip (create → verify). Test that stripping XMP from a registered image causes C2PA path to fail and neural path to pick up.

### Step 5 — Auth middleware
Implement `auth.py`. Write unit tests covering: valid key, expired key, revoked key, malformed key, rate limit exceeded.

### Step 6 — API endpoints
Rewrite `api.py` with the three-path verification logic. Wire together all services. Write integration tests for the full register→verify flow. Test all four status codes (VERIFIED, VERIFIED_MODIFIED, UNREGISTERED, TAMPERED).

### Step 7 — Infrastructure
Write Dockerfiles and docker-compose. Ensure `docker-compose up` brings up a fully working local dev environment with PostgreSQL + Redis + API.

---

## Testing Checklist (must pass before any PR merges)

### Unit tests
- [ ] `payload_codec`: round-trip with 0, 10, 30 injected errors; failure at 31+ errors
- [ ] `neural_watermark_service`: embed produces PSNR ≥ 51; extract round-trips correctly
- [ ] `c2pa_service`: manifest validates against schema; tampered manifest returns `SIGNATURE_INVALID`
- [ ] `auth`: valid key passes; rate-limited key returns 429; revoked key returns 401
- [ ] `api /register`: returns 201 with watermarked image; idempotent on second call with same Idempotency-Key
- [ ] `api /verify`: returns VERIFIED for watermarked image; UNREGISTERED for random image; TAMPERED for stripped+faked manifest

### Integration tests
- [ ] Full flow: register image → download watermarked image → re-upload to verify → status is VERIFIED
- [ ] Robustness flow: register image → apply JPEG q=70 → verify → status is VERIFIED (confidence ≥ 0.7)
- [ ] Strip flow: register image → strip all EXIF/XMP → verify → C2PA path fails → neural path succeeds → VERIFIED
- [ ] pHash flow: register image → apply 10% crop + JPEG q=50 (breaks neural) → verify → pHash path → VERIFIED_MODIFIED
- [ ] Scale: insert 1M random records → Hamming search returns result in < 100ms P99

---

## Common Mistakes to Avoid

1. **Do not store the raw image**. Store only pHash + payload_hex. Images are processed in-memory and discarded.
2. **Do not compute pHash after watermarking**. Always compute pHash on the *original* image before the neural encoder runs, then store that as `image_phash`. The neural encoder shifts pixel values slightly.
3. **Do not use `>=0.5` as the neural confidence threshold**. Use `>= 0.7`. Below 0.7, the extraction is unreliable and you must fall through to pHash.
4. **Do not set Hamming distance threshold above 10**. A threshold of 30 causes ~30% false positive rate on random 64-bit hashes (mean distance is 32).
5. **Do not return the signing private key in any API response or log line**.
6. **Do not call `PIL.Image.open()` on untrusted input without size validation first**. Check image dimensions and format before opening to prevent decompression bombs.
7. **Do not use `SELECT *` in the Hamming search query**. Enumerate columns explicitly so the query plan is predictable.
8. **Do not use synchronous HTTP calls inside Flask request handlers for watermarking**. If latency > 500ms is acceptable for register, use a Celery task. If not, run the neural model directly with a GPU-pinned thread pool.

---

## Reference: Key Library Imports

```python
# Neural watermarking
from trustmark import TrustMark  # pip install trustmark

# Reed-Solomon ECC
import reedsolo  # pip install reedsolo

# C2PA
import c2pa  # pip install c2pa-python

# PostgreSQL
import psycopg2
from psycopg2 import pool

# Redis
import redis

# Image processing
from PIL import Image
import imagehash  # for pHash

# Flask
from flask import Blueprint, request, jsonify, g
from functools import wraps
```

---

## Deliverables for Sprint 1

By the end of Sprint 1 (4 weeks), the following must be functional and tested:

1. `neural_watermark_service.py` — embed and extract, ≥95% bit accuracy on JPEG q=70
2. `payload_codec.py` — 256-bit encode/decode with ECC
3. `c2pa_service.py` — manifest create and verify
4. `db.py` + migrated `provenance.py` — PostgreSQL with pgvector Hamming search
5. `auth.py` — API key validation and rate limiting
6. `api.py` — `/v1/register` and `/v1/verify` with three-path verification
7. `docker-compose.yml` — one-command local dev environment
8. All unit and integration tests passing with ≥80% coverage on `services/`

---

*End of Builder Prompt v1.0*
*Attach: PRD.md, TASKS.md, codebase_analysis.md*
