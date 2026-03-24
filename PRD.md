# Product Requirements Document
## Provena — AI Image Provenance Platform
**Version**: 1.0  
**Status**: Draft  
**Last updated**: March 2026  
**Owner**: Founding Team

---

## 1. Executive Summary

Provena is a model-agnostic AI image provenance platform that enables creators, enterprises, and platforms to register and verify the origin of AI-generated images. It combines a neural watermark engine embedded into image pixels with a C2PA-compliant metadata layer, backed by a fast vector-search registry. The result is a dual-layer provenance signal that survives metadata stripping, social media re-encoding, JPEG compression, and cropping — the attacks that defeat every existing solution.

The platform is exposed as a REST API with usage-based billing, a developer dashboard, and a browser extension for end-user verification. It targets three converging forces: EU AI Act Article 50(2) compliance mandates, enterprise fraud/deepfake detection spend, and the C2PA ecosystem standardization underway at Adobe, Google, Microsoft, Meta, and Sony.

---

## 2. Problem Statement

### 2.1 The Core Problem

AI-generated images are proliferating across social media, news, finance, and legal proceedings with no reliable way to answer: *Who made this? Which model? When?*

Existing approaches each fail in isolation:

| Approach | Weakness |
|---|---|
| EXIF/XMP metadata | Stripped by every social platform, screenshot, or save-as |
| Perceptual hashing only | No payload — tells you it exists in a registry, not who made it |
| Custom DWT/DCT watermarks | ~0% extraction after JPEG compression or crops |
| Detection models (Reality Defender) | Binary classification — not provenance attribution |
| SynthID (Google) | Closed, model-specific, unavailable to third-party AI generators |

### 2.2 The Market Gap

No production-ready, model-agnostic, open-API platform exists that:
- Works on images from *any* generator (Midjourney, DALL-E, Stable Diffusion, Flux, custom fine-tunes)
- Embeds provenance that survives real-world image abuse
- Produces court-admissible, standards-compliant attribution
- Offers this as an API to developers and enterprises

### 2.3 Regulatory Tailwind

The EU AI Act Article 50(2) requires all generative AI providers serving EU users to mark outputs in a machine-readable, detectable format by August 2026. Every AI company without a provenance solution is now accumulating compliance debt.

---

## 3. Goals and Non-Goals

### Goals
- Provide a neural watermarking API that achieves >95% bit accuracy after JPEG compression at quality 70, 10% crop, and social media re-upload
- Produce C2PA-compliant manifests for every registered image
- Enable sub-100ms verification at 10 million registered images
- Ship a developer-facing REST API with SDKs for Python, JavaScript, and Go
- Ship a Chrome extension for consumer-facing provenance checking
- Achieve EU AI Act Article 50(2) technical compliance out of the box

### Non-Goals (v1)
- Video and audio provenance (Phase 3 roadmap)
- On-device/edge watermark embedding
- Zero-knowledge proof layer (Phase 2)
- Building our own generative AI models
- Replacing C2PA — we implement and extend it

---

## 4. Target Users

### 4.1 Primary: AI Platform Developers
Companies building products on top of AI image generators (Midjourney API wrappers, stock photo generators, AI avatar tools) who need to add provenance to their output pipeline. They interact via API.

**Jobs to be done**: Comply with EU AI Act, protect brand reputation, add "Content Credentials" badge to images.

### 4.2 Secondary: Enterprise Security Teams
Banks, insurance companies, legal discovery firms, and media organizations who need to verify whether an image submitted to them is AI-generated and who made it.

**Jobs to be done**: KYC fraud prevention, deepfake litigation evidence, newsroom authentication.

### 4.3 Tertiary: End Users / Journalists
People browsing the web who encounter a suspicious image and want to know its origin. They interact via the browser extension.

**Jobs to be done**: Verify a viral image before sharing, check a news photo's authenticity.

---

## 5. System Architecture

### 5.1 High-Level Architecture

```
                          ┌─────────────────────────────────────┐
                          │           Client Layer               │
                          │  REST API · Browser Extension · SDK  │
                          └──────────────┬──────────────────────┘
                                         │
                          ┌──────────────▼──────────────────────┐
                          │           API Gateway                │
                          │  Auth · Rate limiting · Metering     │
                          └──────────────┬──────────────────────┘
                                         │
               ┌─────────────────────────┼─────────────────────────┐
               │                         │                         │
   ┌───────────▼──────────┐  ┌───────────▼──────────┐  ┌──────────▼───────────┐
   │  Neural Watermark    │  │   C2PA Manifest       │  │   Provenance DB      │
   │  Encoder/Decoder     │  │   Generator           │  │   PostgreSQL +       │
   │  (TrustMark/Seal)    │  │   (c2pa-python)       │  │   pgvector           │
   └──────────────────────┘  └──────────────────────┘  └──────────────────────┘
```

### 5.2 Registration Flow

1. Client POSTs image + metadata (model_id, creator_did, timestamp, custom fields) to `/v1/register`
2. API validates, generates a 256-bit payload: `[model_id:8][timestamp:40][session:64][phash_checksum:48][ECC:96]`
3. Neural encoder embeds payload into image pixels (imperceptible, PSNR ≥ 51)
4. C2PA manifest is generated, cryptographically signed with our Ed25519 key, embedded in XMP/EXIF
5. Provenance record is written to PostgreSQL with pHash vector for similarity search
6. Watermarked image + manifest JSON returned to client

### 5.3 Verification Flow

1. Client POSTs image to `/v1/verify`
2. **Fast path**: Check XMP/EXIF for C2PA manifest. If valid signature found → return provenance record immediately
3. **Fallback path**: Neural decoder extracts 256-bit payload from pixels. Decode model_id, timestamp, session from payload. Look up in DB → return provenance record
4. **pHash fallback**: If extraction confidence < 0.7, compute pHash, run pgvector Hamming-distance query (radius ≤ 10 bits). Return closest match with confidence score
5. Response includes: `status`, `confidence`, `registered_at`, `model_id`, `creator_did`, `manifest_url`, `watermark_present`

### 5.4 Payload Structure (256 bits)

| Field | Bits | Description |
|---|---|---|
| model_id | 8 | Registered model index (256 model namespace) |
| timestamp | 40 | Unix epoch seconds, valid until year 2109 |
| session_token | 64 | Truncated HMAC of (user_id + nonce) |
| phash_checksum | 48 | First 48 bits of original image pHash |
| reed_solomon_ecc | 96 | Corrects up to 30 burst bit errors |

---

## 6. Feature Specifications

### 6.1 Neural Watermark Engine

**Requirements**:
- PSNR ≥ 51 dB (imperceptible quality loss)
- SSIM ≥ 0.998
- Bit accuracy ≥ 95% after: JPEG q=70, 10% random crop, 512px resize, Gaussian noise σ=0.05, social media re-upload simulation
- Payload capacity: 256 bits
- Embedding latency: < 500ms on GPU (T4 or better)
- Extraction latency: < 200ms on GPU

**Implementation**: TrustMark (open-source, MIT) as base, fine-tuned on augmented dataset of 500K AI-generated images. Fall back to Meta Seal for higher capacity use cases.

**Robustness attacks to train against**:
- JPEG compression (q=50–95)
- Random crop (5–15%)
- Gaussian blur (σ=0–2)
- Additive noise
- Brightness/contrast shift (±20%)
- Horizontal flip
- Social media simulation (Twitter/Instagram pipeline)

### 6.2 C2PA Manifest Layer

**Requirements**:
- Generate valid C2PA 2.0 manifests for every registered image
- Sign manifests with Ed25519 key pair stored in AWS KMS
- Embed in XMP `dc:description` and EXIF UserComment fields
- Serve manifests at stable URL: `https://api.provena.io/manifests/{manifest_id}`
- Verify manifests from third-party issuers (Adobe, Leica, Sony cameras)
- Manifest must include: `c2pa.created`, `c2pa.ai.generativeml.training_and_inference` assertion

**Implementation**: `c2pa-python` library (Content Authenticity Initiative official SDK)

### 6.3 Provenance Registry

**Requirements**:
- PostgreSQL 16+ with `pgvector` extension
- pHash stored as `bit(64)` with IVFFlat index for approximate nearest-neighbor search
- Hamming-distance query: radius ≤ 10 bits, sub-100ms at 10M records
- Append-only writes (no UPDATE, no DELETE on provenance records)
- Unique constraint on `(model_id, creator_did, image_phash, timestamp)` with 1-second dedup window
- Full audit log of all reads (for legal discovery)

**Schema** (core tables):

```
provenance_records:
  id UUID PRIMARY KEY
  image_phash bit(64) [indexed, ivfflat]
  model_id varchar(64)
  creator_did varchar(256)
  timestamp timestamptz NOT NULL
  payload_hex varchar(64)
  manifest_id UUID FK
  api_key_id UUID FK
  created_at timestamptz DEFAULT now()

manifests:
  id UUID PRIMARY KEY
  c2pa_json jsonb
  signature bytea
  public_key_id varchar(64)
  created_at timestamptz

api_keys:
  id UUID PRIMARY KEY
  key_hash varchar(64) UNIQUE
  org_id UUID
  created_at timestamptz
  last_used_at timestamptz
  is_active bool DEFAULT true
```

### 6.4 REST API

**Base URL**: `https://api.provena.io/v1`

**Authentication**: Bearer token (`Authorization: Bearer prov_sk_...`)

**Endpoints**:

| Method | Path | Description |
|---|---|---|
| POST | `/register` | Register an image, embed watermark + C2PA manifest |
| POST | `/verify` | Verify image provenance |
| GET | `/manifests/{id}` | Retrieve a C2PA manifest by ID |
| GET | `/records/{id}` | Retrieve a provenance record |
| GET | `/usage` | API usage stats for current billing period |
| POST | `/keys` | Create a new API key |
| DELETE | `/keys/{id}` | Revoke an API key |

**`POST /register` request**:
```json
{
  "image": "<base64-encoded image or URL>",
  "model_id": "stable-diffusion-xl-1.0",
  "creator_did": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
  "custom_fields": { "prompt_hash": "sha256:abc123..." }
}
```

**`POST /register` response**:
```json
{
  "record_id": "rec_01HZ...",
  "manifest_id": "mfst_01HZ...",
  "watermarked_image": "<base64>",
  "manifest_url": "https://api.provena.io/v1/manifests/mfst_01HZ...",
  "phash": "a3f4b2c1d9e8...",
  "registered_at": "2026-03-20T10:00:00Z"
}
```

**`POST /verify` response**:
```json
{
  "status": "VERIFIED" | "VERIFIED_MODIFIED" | "UNREGISTERED" | "TAMPERED",
  "confidence": 0.97,
  "record_id": "rec_01HZ...",
  "registered_at": "2026-03-20T10:00:00Z",
  "model_id": "stable-diffusion-xl-1.0",
  "creator_did": "did:key:...",
  "watermark_extracted": true,
  "c2pa_valid": true,
  "hamming_distance": 4
}
```

**Status definitions**:
- `VERIFIED`: Neural watermark extracted successfully OR C2PA manifest valid. High confidence.
- `VERIFIED_MODIFIED`: pHash match found but watermark not extracted (image was modified post-watermark, or is the unmodified original). Medium confidence.
- `UNREGISTERED`: No match found within Hamming distance ≤ 10. Image not in registry.
- `TAMPERED`: C2PA manifest found but signature invalid. Active tampering detected.

### 6.5 Rate Limiting and Billing

| Tier | Register | Verify | Price |
|---|---|---|---|
| Free | 100/day | 500/day | $0 |
| Starter | 10K/month | 50K/month | $49/month |
| Growth | 500K/month | 2.5M/month | $499/month |
| Enterprise | Unlimited | Unlimited | Custom |

Overage: $0.001 per register, $0.0005 per verify.

### 6.6 Developer Dashboard

Single-page React app at `dashboard.provena.io`:

- API key management (create, revoke, view last-used)
- Usage charts (registers/verifies per day, rolling 30 days)
- Recent registrations table with image thumbnails
- Billing overview and invoice history
- Webhook configuration for async verification results
- OpenAPI documentation viewer (Swagger UI)

### 6.7 Browser Extension (Chrome + Firefox)

Functionality:
- Right-click any image on any webpage → "Check provenance"
- Calls `/v1/verify` with the image URL
- Displays a badge overlay: Green (VERIFIED), Yellow (VERIFIED_MODIFIED), Red (TAMPERED), Gray (UNREGISTERED)
- Clicking badge opens a provenance detail panel showing model, creator, timestamp, manifest link
- Optional: persistent icon in address bar showing page's overall provenance score

---

## 7. Non-Functional Requirements

### 7.1 Performance
- `/register` P99 latency: < 2000ms (GPU path)
- `/verify` P99 latency: < 500ms (neural path), < 100ms (C2PA fast path)
- API availability: 99.9% SLA
- Database reads: < 50ms P99 at 10M records

### 7.2 Security
- API keys: HMAC-SHA256 hashed at rest, never logged
- Images: not stored after processing unless opt-in retention is enabled
- C2PA signing keys: stored in AWS KMS, never in application memory
- All endpoints: TLS 1.3 minimum
- SOC 2 Type II roadmap: 18 months post-launch

### 7.3 Privacy
- Images processed in-memory only; discarded after watermark embedding
- pHash and payload stored, never raw image pixels
- GDPR-compliant: creator_did is under creator's control; deletion requests honored for non-provenance fields
- Audit logs retained 7 years (legal discovery requirement)

### 7.4 Compliance
- C2PA Specification 2.0 compliant
- EU AI Act Article 50(2) compliant (machine-readable mark, interoperable, robust)
- GDPR Article 25 (privacy by design)

---

## 8. Success Metrics

### Phase 1 (0–6 months)
- Watermark bit accuracy ≥ 95% on robustness benchmark suite
- API `/verify` P99 < 500ms at 1M registered images
- 50 developer signups in beta
- 3 pilot enterprise customers

### Phase 2 (6–18 months)
- 1M images registered
- 10 paying enterprise customers
- $250K ARR
- C2PA verification interoperable with Adobe's Content Authenticity Initiative viewer

### Phase 3 (18–36 months)
- 100M images registered
- $2M ARR
- Browser extension: 50K weekly active users
- Multi-modal: video support launched

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Neural watermark broken by adversarial attacks | Medium | High | Ensemble multiple models; monitor attack literature; ship model updates as patches |
| C2PA metadata stripped by social platforms | High | Medium | This is expected — pixel watermark is the fallback; dual-layer design anticipates this |
| False positive rate causes user trust issues | Medium | High | Conservative Hamming threshold (≤ 10); confidence score always returned; human review API for disputes |
| GPU cost makes free tier unsustainable | Medium | Medium | CPU-optimized extraction path for verify; GPU only for register |
| Competitor (SynthID) opens API | Low | Medium | Our moat is model-agnosticism and C2PA compliance — SynthID is Google-model-specific |

---

## 10. Open Questions

1. Should we support `did:web` as well as `did:key` for creator identity, to support enterprise DID infrastructure?
2. What is the retention policy for watermarked image thumbnails in the dashboard?
3. Do we expose the neural model weights, or keep them proprietary to prevent targeted adversarial attacks against our specific decoder?
4. Should the browser extension work offline against a local model, or always call the API?

---

*End of PRD v1.0*
