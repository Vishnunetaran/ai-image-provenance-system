# Provena — Engineering Task List
**Derived from**: PRD v1.0  
**Total tasks**: 87  
**Format**: Epic > Story > Task

---

## EPIC 1 — Neural Watermark Engine
*Replace the broken DWT/DCT implementation with a production-grade neural encoder-decoder*

### Story 1.1 — Baseline benchmark
- [x] **T-001** Set up a robustness benchmark dataset: download 5,000 images from LAION-Aesthetics subset, generate 5,000 images from Stable Diffusion XL, save to `/data/benchmark/`
- [x] **T-002** Write `benchmark/run_attack.py`: applies JPEG (q=50,70,90), 10% crop, Gaussian blur (σ=1,2), resize to 512px, brightness ±20%, and simulated Twitter/Instagram pipeline to each image
- [x] **T-003** Run current DWT+DCT watermark against benchmark suite; record bit accuracy per attack. Baseline target: <10% (expected to confirm near-0%)
- [x] **T-004** Document baseline numbers in `benchmark/results/baseline.json`

### Story 1.2 — TrustMark integration
- [x] **T-005** Install `invisible-watermark` and `trustmark` Python packages; pin versions in `requirements.txt`
- [x] **T-006** Create `provena_flask/services/neural_watermark_service.py` with `embed(image: PIL.Image, payload: bytes) -> PIL.Image` and `extract(image: PIL.Image) -> tuple[bytes, float]` interfaces
- [x] **T-007** Implement `embed()` using TrustMark Q encoder (highest quality variant). Accept 256-bit payload as input. Return watermarked PIL image.
- [x] **T-008** Implement `extract()` using TrustMark decoder. Return `(payload_bytes, confidence_float)`. Confidence < 0.7 triggers pHash fallback.
- [x] **T-009** Write unit tests: `tests/test_neural_watermark.py` — round-trip embed/extract, verify payload integrity, verify PSNR ≥ 51

### Story 1.3 — 256-bit payload codec
- [x] **T-010** Create `provena_flask/services/payload_codec.py` with `encode(model_id, timestamp, session_token, phash_bytes) -> bytes` (256 bits) and `decode(payload: bytes) -> dict`
- [x] **T-011** Implement Reed-Solomon ECC using `reedsolo` library: encode 160-bit data into 256-bit codeword (96-bit redundancy, corrects 30 burst errors)
- [x] **T-012** Write unit tests: `tests/test_payload_codec.py` — verify round-trip with 0, 10, 30 injected bit errors; verify failure at 31+ errors

### Story 1.4 — Robustness validation
- [x] **T-013** Run benchmark suite against TrustMark integration. Target: ≥95% bit accuracy on JPEG q=70 and 10% crop
- [x] **T-014** If JPEG bit accuracy < 95%: fine-tune TrustMark on augmented dataset (500K images, 10 epochs, JPEG augmentation in training loop)
- [x] **T-015** Run PSNR and SSIM measurement script across 1,000 image pairs. Assert PSNR ≥ 51, SSIM ≥ 0.998
- [x] **T-016** Document final benchmark results in `benchmark/results/trustmark_v1.json`
- [x] **T-017** Delete `provena_flask/services/watermark_service.py` (old DWT/DCT implementation)

---

## EPIC 2 — C2PA Manifest Layer
*Add standards-compliant provenance metadata alongside the pixel watermark*

### Story 2.1 — C2PA library setup
- [x] **T-018** Install `c2pa-python` (Content Authenticity Initiative official SDK); add to `requirements.txt`
- [x] **T-019** Generate Ed25519 key pair for signing. Store private key in AWS KMS (or local `.env` for dev). Store public key fingerprint in `config/c2pa_keys.json`
- [x] **T-020** Write `provena_flask/services/c2pa_service.py` with `create_manifest(image, metadata) -> (manifest_json, signed_image_bytes)` and `verify_manifest(image) -> ManifestResult`

### Story 2.2 — Manifest generation
- [x] **T-021** Implement `create_manifest()`: populate `c2pa.created` assertion with timestamp and creator DID; populate `c2pa.ai.generativeml.training_and_inference` assertion with model_id; sign with Ed25519
- [x] **T-022** Embed manifest in XMP `dc:description` and EXIF `UserComment` fields using `piexif` library
- [x] **T-023** Store manifest JSON in `manifests` DB table; return `manifest_id`
- [x] **T-024** Write unit tests: `tests/test_c2pa_service.py` — create manifest, verify it validates against C2PA schema, verify signature

### Story 2.3 — Manifest verification
- [x] **T-025** Implement `verify_manifest()`: extract XMP/EXIF from image, parse C2PA manifest JSON, verify Ed25519 signature against stored public key
- [x] **T-026** Handle third-party manifest verification (Adobe CAI, Sony camera, Leica): accept any C2PA 2.0 manifest signed by a trusted root in our trust list
- [x] **T-027** Write integration test: register image → strip XMP manually → verify that pixel watermark fallback correctly activates

---

## EPIC 3 — Database Migration
*Replace SQLite with PostgreSQL + pgvector for production-scale similarity search*

### Story 3.1 — PostgreSQL setup
- [x] **T-028** Add `psycopg2-binary` and `pgvector` to `requirements.txt`
- [x] **T-029** Write `migrations/001_initial_schema.sql`: create `provenance_records`, `manifests`, `api_keys` tables per PRD schema section 6.3
- [x] **T-030** Add `pgvector` extension activation in migration: `CREATE EXTENSION IF NOT EXISTS vector;`
- [x] **T-031** Create IVFFlat index on `provenance_records.image_phash`: `CREATE INDEX ON provenance_records USING ivfflat (image_phash bit_hamming_ops) WITH (lists = 100);`
- [x] **T-032** Write `migrations/002_audit_log.sql`: create append-only `verification_log` table with `record_id`, `api_key_id`, `queried_at`, `hamming_distance`, `status_returned`

### Story 3.2 — Data access layer
- [x] **T-033** Refactor `provena_flask/models/provenance.py`: replace SQLite connection with `psycopg2` pool (min=2, max=20 connections)
- [x] **T-034** Implement `find_by_hamming(phash: int, max_distance: int = 10) -> list[ProvenanceRecord]` using pgvector `<~>` operator (Hamming distance)
- [x] **T-035** Implement append-only `insert_record()` that raises if a duplicate within 1-second window and same phash is detected
- [x] **T-036** Implement `get_record_by_id()` with read replica routing if replica env var is set
- [x] **T-037** Write load test: `tests/load/test_phash_search.py` — verify P99 < 50ms at 1M records using `locust` or `pytest-benchmark`

### Story 3.3 — Redis caching
- [x] **T-038** Add `redis` Python package; configure `REDIS_URL` env var
- [x] **T-039** Add LRU cache in `verify_image`: cache `{phash_hex} → ProvenanceRecord` for 60 seconds, max 10K entries. Use Redis `SETEX` with TTL
- [x] **T-040** Add cache invalidation on new registration if phash matches a cached entry

---

## EPIC 4 — API Layer Refactor
*Replace Flask demo endpoints with a production REST API*

### Story 4.1 — Authentication and rate limiting
- [x] **T-041** Create `provena_flask/auth.py`: API key validation middleware. Keys are `prov_sk_{base62(32bytes)}`. Store `SHA256(key)` in DB, never plaintext.
- [x] **T-042** Implement rate limiting using `flask-limiter` with Redis backend. Apply per-key limits: 100/day free, 10K/month starter (configurable from DB)
- [x] **T-043** Add `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` response headers
- [x] **T-044** Implement API key creation endpoint `POST /v1/keys`: generate key, store hash, return key once (never again)
- [x] **T-045** Implement API key revocation `DELETE /v1/keys/{id}`: soft delete (set `is_active = false`)

### Story 4.2 — Register endpoint
- [x] **T-046** Rewrite `POST /v1/register` in `provena_flask/blueprints/api.py`:
  1. Accept `multipart/form-data` (image file) or `application/json` (base64 or URL)
  2. Validate image: must be PNG/JPEG/WebP, max 10MB, min 256×256px
  3. Compute original pHash *before* watermarking
  4. Generate 256-bit payload via `payload_codec.encode()`
  5. Call `neural_watermark_service.embed()` → watermarked image
  6. Call `c2pa_service.create_manifest()` → signed manifest + manifest_id
  7. Write to `provenance_records` table
  8. Return JSON per PRD spec
- [x] **T-047** Add input validation: reject images with existing C2PA manifest from a *different* issuer (conflict detection)
- [x] **T-048** Add idempotency: `Idempotency-Key` header support; cache result for 24h in Redis

### Story 4.3 — Verify endpoint
- [x] **T-049** Rewrite `POST /v1/verify` in `provena_flask/blueprints/api.py`:
  1. Accept same input formats as register
  2. **Fast path**: call `c2pa_service.verify_manifest()`. If valid → look up record, return VERIFIED
  3. **Neural path**: call `neural_watermark_service.extract()`. If confidence ≥ 0.7 → decode payload → look up record → return VERIFIED
  4. **pHash fallback**: compute pHash, call `find_by_hamming(phash, max_distance=10)`. If match → return VERIFIED_MODIFIED
  5. If no match: return UNREGISTERED
  6. If C2PA manifest found but signature invalid: return TAMPERED
- [x] **T-050** Remove the `exact_phash_match == Original` hack (formerly used to distinguish original vs watermarked). Replace with extraction confidence threshold.
- [x] **T-051** Write verification audit log entry for every verify call (required for legal discovery)

### Story 4.4 — Remaining endpoints
- [x] **T-052** Implement `GET /v1/manifests/{id}`: return C2PA manifest JSON with `Content-Type: application/c2pa+json`
- [x] **T-053** Implement `GET /v1/records/{id}`: return full provenance record (redact session_token bits)
- [x] **T-054** Implement `GET /v1/usage`: aggregate current billing period register/verify counts per API key
- [x] **T-055** Add OpenAPI 3.1 spec: write `openapi.yaml` covering all endpoints with request/response schemas

### Story 4.5 — Error handling and observability
- [x] **T-056** Standardize error responses: `{"error": {"code": "INVALID_IMAGE", "message": "...", "request_id": "..."}}`
- [x] **T-057** Add `X-Request-ID` header (UUID) to every response; log it in Sentry
- [x] **T-058** Add Prometheus metrics: `provena_register_duration_seconds`, `provena_verify_duration_seconds`, `provena_watermark_confidence_histogram`
- [x] **T-059** Add structured JSON logging with `python-json-logger`: include `api_key_id`, `request_id`, `duration_ms`, `status_code` in every log line

---

## EPIC 5 — Infrastructure and Deployment
*Containerize and deploy to production-grade cloud infrastructure*

### Story 5.1 — Containerization
- [ ] **T-060** Write `Dockerfile.api`: Python 3.11 base, install CUDA 12.x for GPU inference, `requirements.txt`, gunicorn entrypoint
- [ ] **T-061** Write `Dockerfile.worker`: same base, Celery worker entrypoint for async watermark jobs
- [ ] **T-062** Write `docker-compose.yml`: `api`, `worker`, `postgres`, `redis` services with health checks
- [ ] **T-063** Write `docker-compose.gpu.yml` override: GPU device reservation for `api` and `worker` services

### Story 5.2 — Cloud deployment
- [ ] **T-064** Write Terraform module for AWS: ECS Fargate (api), ECS with GPU task (worker), RDS PostgreSQL 16 with pgvector, ElastiCache Redis, ALB with TLS, Route53
- [ ] **T-065** Configure AWS KMS key for C2PA signing; grant ECS task role `kms:Sign` permission only
- [ ] **T-066** Set up GitHub Actions CI: lint (ruff), type-check (mypy), unit tests, Docker build on every PR
- [ ] **T-067** Set up GitHub Actions CD: deploy to staging on merge to `main`; deploy to production on tag `v*`
- [ ] **T-068** Configure CloudWatch alarms: P99 latency > 2s, error rate > 1%, GPU utilization < 10% (worker idle alert)

---

## EPIC 6 — Developer Dashboard
*React SPA for API key management, usage monitoring, and documentation*

### Story 6.1 — Project setup
- [ ] **T-069** Initialize React project with Vite + TypeScript at `dashboard/`; add TailwindCSS, React Query, Recharts, React Router
- [ ] **T-070** Set up auth: Clerk or Auth0 for Google/GitHub OAuth; gate all routes behind authentication

### Story 6.2 — Core pages
- [ ] **T-071** **Keys page**: table of API keys (name, created_at, last_used_at, status). Create key button → modal showing key once. Revoke button with confirmation.
- [ ] **T-072** **Usage page**: line chart (Recharts) of registers/verifies per day, rolling 30 days. Metric cards: total registered this period, total verified, estimated bill.
- [ ] **T-073** **Recent registrations page**: paginated table with thumbnail (generated from pHash — not stored image), model_id, created_at, manifest link
- [ ] **T-074** **Billing page**: current plan badge, usage vs. limit progress bar, invoice history table, upgrade button
- [ ] **T-075** **Docs page**: embed Swagger UI pointed at `/openapi.yaml`; add "Try it" panels pre-populated with user's API key

### Story 6.3 — Webhooks
- [ ] **T-076** Add webhook config UI: HTTPS endpoint URL + secret, event type selector (`registration.created`, `verification.completed`), test-fire button
- [ ] **T-077** Implement webhook delivery in API worker: sign payload with HMAC-SHA256 using webhook secret, POST to endpoint, retry 3× with exponential backoff

---

## EPIC 7 — Browser Extension
*Chrome + Firefox extension for consumer-facing provenance checking*

### Story 7.1 — Extension scaffold
- [ ] **T-078** Set up extension project with WXT (cross-browser framework) at `extension/`; Manifest V3
- [ ] **T-079** Implement right-click context menu: "Check image provenance with Provena" on `<img>` elements

### Story 7.2 — Verify flow
- [ ] **T-080** On context menu click: fetch image as blob, POST to `https://api.provena.io/v1/verify` using anonymous public key (rate-limited to 50/day per extension install)
- [ ] **T-081** Display result as injected overlay badge on the image: Green/Yellow/Red/Gray with icon and one-line status
- [ ] **T-082** On badge click: open popover showing model_id, creator_did, registered_at, manifest URL, confidence score, and link to full record on dashboard

### Story 7.3 — Distribution
- [ ] **T-083** Submit to Chrome Web Store
- [ ] **T-084** Submit to Firefox Add-on Store
- [ ] **T-085** Write extension landing page at `provena.io/extension`

---

## EPIC 8 — SDK and Developer Experience

- [ ] **T-086** Write Python SDK `provena-python`: thin wrapper around REST API, typed with Pydantic, async-first (httpx). Publish to PyPI.
- [ ] **T-087** Write JavaScript/TypeScript SDK `@provena/sdk`: ESM + CJS builds, typed with Zod, Node + browser. Publish to npm.

---

## Priority Order for Sprint 1 (Weeks 1–4)

| Week | Tasks |
|---|---|
| 1 | T-001, T-002, T-003, T-005, T-006, T-007, T-008 |
| 2 | T-009, T-010, T-011, T-012, T-013, T-028, T-029, T-030 |
| 3 | T-031, T-033, T-034, T-035, T-041, T-042, T-046 |
| 4 | T-049, T-050, T-018, T-019, T-020, T-021, T-022 |

---

*End of Task List v1.0*
