# PROVENA-FLASK

**AI Image Provenance & Forensic Verification System**

A cryptographic provenance tracking and forensic verification system for AI-generated images, where **cryptographic signatures and append-only registry provide authoritative proof of origin**, supplemented by forensic watermarking traces.

---

## Overview

PROVENA-FLASK is a research implementation of a comprehensive AI image provenance system that establishes **cryptographic traceability** for AI-generated images. The system uses Ed25519 digital signatures and an append-only registry as the authoritative proof of origin, with invisible watermarking providing supplementary forensic evidence when extractable.

### Core Principle

**Cryptographic Provenance, Not Watermark Detection**

This system is fundamentally a **cryptographic provenance platform**, not a watermarking product. Verification relies primarily on:

1. **Ed25519 Digital Signatures** (Primary) - Cryptographic proof of metadata integrity
2. **Append-Only Registry** (Primary) - Tamper-evident provenance storage
3. **Perceptual Hashing** (Secondary) - Tolerant image matching
4. **Invisible Watermarking** (Supplementary) - Forensic trace when extractable

---

## What This System Proves

### ✅ Authoritative Cryptographic Proof

- **Metadata Integrity**: Image was registered with specific metadata (model, timestamp)
- **Tamper Detection**: Metadata has not been altered (cryptographic signature)
- **Provenance Chain**: Complete audit trail in append-only registry
- **Perceptual Similarity**: Image is perceptually similar to registered image

### ⚠️ Supplementary Forensic Evidence

- **Watermark Presence**: Invisible watermark may provide additional forensic trace
- **Watermark Extraction**: Probabilistic, may fail under compression/resizing
- **Forensic Signal**: Watermark is supporting evidence, NOT primary proof

### ❌ What This System Does NOT Prove

- **Pixel-Level Integrity**: Cannot prove image pixels are unmodified
- **Timestamp Accuracy**: Timestamps are self-reported, not independently verified
- **Image Authenticity**: Only proves registration, not that image is "real"
- **Perfect Detection**: Watermark extraction is probabilistic and may fail

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                   CRYPTOGRAPHIC PROVENANCE LAYER                 │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Ed25519 Digital Signatures (PRIMARY PROOF)            │     │
│  │  • Metadata signing with private key                   │     │
│  │  • Signature verification with public key              │     │
│  │  • Cryptographically unforgeable                       │     │
│  └────────────────────────────────────────────────────────┘     │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Append-Only Registry (AUTHORITATIVE STORAGE)          │     │
│  │  • SQLite with append-only constraints                 │     │
│  │  • Tamper-evident provenance records                   │     │
│  │  • Complete audit trail                                │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FORENSIC VERIFICATION LAYER                   │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Perceptual Hashing (SECONDARY VERIFICATION)           │     │
│  │  • pHash, dHash, aHash                                 │     │
│  │  • Tolerant to minor modifications                     │     │
│  │  • Hamming distance matching                           │     │
│  └────────────────────────────────────────────────────────┘     │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Invisible Watermarking (SUPPLEMENTARY TRACE)          │     │
│  │  • Hybrid DWT+DCT embedding                            │     │
│  │  • Forensic trace when extractable                     │     │
│  │  • May fail under compression/resizing                 │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

### Verification Hierarchy

**Tier 1 (Authoritative)**: Cryptographic Signature + Registry  
**Tier 2 (Robust)**: Perceptual Hash Matching  
**Tier 3 (Supplementary)**: Watermark Extraction  

---

## Key Features

### 🔐 Cryptographic Provenance (Primary)

- **Ed25519 Digital Signatures**: Industry-standard elliptic curve cryptography
- **Append-Only Registry**: Tamper-evident SQLite database
- **Key Management**: Secure key generation, storage, and rotation
- **Audit Logging**: Complete operation tracking

### 🔍 Forensic Verification (Secondary)

- **Perceptual Hashing**: DCT-based pHash, gradient-based dHash, average-based aHash
- **Tolerant Matching**: Hamming distance with configurable threshold
- **Robust to Modifications**: Survives compression, resizing, minor edits

### 🎨 Forensic Watermarking (Supplementary)

- **Hybrid DWT+DCT (v2.0)**: Frequency-domain embedding on the luminance channel
- **Imperceptible**: PSNR ≥45dB, SSIM ≥0.99 targets (configurable)
- **Robustness aids**: 5x redundant bit embedding with majority voting, 16-bit sync prefix, repetition-coded payload
- **Forensic Trace**: Provides additional evidence when extractable
- **Honest Limitations**: Can still be lost under heavy JPEG (Q<50), cropping, rotation, or aggressive filtering

### 📊 Forensic Reporting

- **5-Level Verdict System**: authentic, likely_authentic, suspicious, tampered, not_found
- **Confidence Scoring**: 0.0-1.0 scale based on multiple verification layers
- **Evidence Summary**: Clear breakdown of cryptographic, perceptual, and watermark evidence
- **Limitations Disclosure**: Every report includes known system limitations

### 🛡️ Security & Audit

- **Rate Limiting**: 60 requests/minute per IP
- **Input Validation**: SQL injection prevention, size limits
- **Structured Logging**: JSON-formatted audit trails
- **Security Event Tracking**: Comprehensive attack detection

---

## API Endpoints

All JSON endpoints accept and return `application/json`. Images are passed as base64-encoded strings (max 16 MB; allowed types: png, jpg, jpeg, webp).

### Core Provenance APIs

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/images/register` | Establish cryptographic provenance for an AI-generated image. Returns signature, registry record, and watermarked image. |
| `POST` | `/api/v1/images/verify` | Verify an image. Returns verdict (`verified`, `verified_modified`, `tampered`, `not_found`) plus per-layer signals (signature, perceptual hash, watermark). |
| `GET` | `/api/v1/provenance/<image_id>` | Retrieve the full provenance record (metadata, signature, public key, timestamps). |
| `GET` | `/api/v1/report/<image_id>` | Generate a forensic report. Supports `?format=text` for human-readable output; default is JSON. The same report is also served at `/reports/<image_id>`. |

### Status / Health

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | App-level health check. |
| `GET` | `/api/v1/status` | API liveness. |
| `GET` | `/registry/status`, `/watermark/status`, `/verification/status`, `/reports/status` | Per-service liveness. |
| `GET` | `/` | Demo UI (drag-and-drop register/verify, JSON inspection, forensic report viewer). |

### Stub routes (return 501)

`GET /registry/records`, `POST /watermark/embed`, `POST /watermark/extract`, and `POST /verification/check` are scaffolded but not implemented — the embedded/extracted operations happen inside `/api/v1/images/register` and `/api/v1/images/verify`.

---

## System Limitations

### Watermark Robustness (Known Limitation)

**Limitation**: Watermark extraction is probabilistic. The v2.0 hybrid DWT+DCT engine with redundancy and synchronization significantly improves resilience over a basic DWT scheme, but extraction can still fail under aggressive transformations.

**Designed to survive**: JPEG Q≥75, ±10% resizing, basic format conversion (PNG↔JPEG).

**Will likely fail under**: extreme compression (Q<50), cropping, rotation, heavy filtering, or geometric attacks beyond the sync window.

**Impact**: Watermark provides supplementary forensic trace only. **Cryptographic verification remains authoritative** even when watermark extraction fails — the `verified_modified` verdict is the expected outcome in that case, not a failure.

**Mitigation**: System relies primarily on cryptographic signatures and perceptual hashing, which are robust and reliable.

### Timestamp Trust

**Limitation**: Timestamps are self-reported by AI model provider, not independently verified.

**Impact**: Cannot cryptographically prove when image was generated.

**Mitigation**: Trust model relies on reputation of AI model provider. Future: blockchain timestamping.

### Pixel-Level Integrity

**Limitation**: System proves metadata integrity, not pixel-by-pixel identity.

**What We Prove**: Metadata signed, perceptual similarity  
**What We Don't Prove**: Pixels unmodified, no post-processing

**Impact**: Verified image may have been edited after generation.

**Mitigation**: Perceptual hash catches major changes; minor edits are undetectable.

### Blind Watermarking

**Limitation**: Watermark extraction is "blind" (no original image reference).

**Impact**: Extraction relies on absolute coefficient values, which are unreliable after compression.

**Mitigation**: Watermark is supplementary only. Cryptographic verification is primary.

### Centralized Trust

**Limitation**: Registry is centralized, not distributed.

**Impact**: Trust in system operator required. Single point of failure.

**Mitigation**: Append-only constraints prevent tampering. Future: blockchain integration.

---

## Relation to Industry Standards

### Conceptual Alignment

This system is **conceptually aligned** with cryptographic provenance models used in industry standards:

- **C2PA (Coalition for Content Provenance and Authenticity)**: Uses cryptographic signatures and manifest files
- **Adobe Content Credentials**: Embeds provenance metadata with digital signatures
- **OpenAI Metadata Approaches**: Cryptographic signing of AI-generated content

### Key Differences

**This is NOT a C2PA implementation**, but shares the same fundamental principle:

> **Cryptographic signatures provide authoritative proof of provenance, not watermarks.**

**Similarities**:
- Digital signatures for metadata integrity
- Tamper-evident provenance storage
- Multi-layer verification approach

**Differences**:
- C2PA uses JUMBF manifests; we use SQLite registry
- C2PA has broader scope (photos, videos, documents); we focus on AI images
- C2PA is production-ready; this is a research implementation

---

## Installation

### Prerequisites

- Python 3.8+
- Virtual environment (recommended)

### Setup

```bash
# Clone repository
git clone <repository-url>
cd ai-image-provenance-system

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (Optional) copy and edit environment file
cp .env.example .env
```

### Run Server

```bash
python run.py
```

Server starts on `http://0.0.0.0:5000`. The demo UI is at `http://localhost:5000/`, and a health check is exposed at `/health`.

The first call to `/api/v1/images/register` will auto-generate an Ed25519 key pair under `./keys/` and create the SQLite registry under `./data/`. Both directories are git-ignored.

---

## Usage

### Demo Web Interface

Open browser to `http://localhost:5000/`

**Features**:
- Drag-and-drop image upload
- Register image with cryptographic provenance
- Verify image using multi-layer forensic analysis
- View comprehensive forensic reports

### API Usage (Python)

```python
import requests
import base64

# Register image
with open('image.png', 'rb') as f:
    image_b64 = base64.b64encode(f.read()).decode()

response = requests.post('http://localhost:5000/api/v1/images/register', json={
    'image': image_b64,
    'model_id': 'gpt-vision-v1',
    'timestamp': '2026-01-26T19:00:00Z'
})

result = response.json()
print(f"Image ID: {result['image_id']}")
print(f"Signature: {result['signature'][:50]}...")

# Verify image
response = requests.post('http://localhost:5000/api/v1/images/verify', json={
    'image': image_b64
})

result = response.json()
print(f"Verdict: {result['status']}")
print(f"Signature Valid: {result['verification']['signature_valid']}")
print(f"Perceptual Match: {result['verification']['perceptual_match']}")
print(f"Watermark Present: {result['verification']['watermark_extracted']}")
```

---

## Testing

Each script is a standalone runner that exits non-zero on failure. With the Flask server stopped (most are unit tests), run any of:

```bash
python test_crypto.py                    # Ed25519 sign/verify
python test_registry.py                  # Append-only registry semantics
python test_phash.py                     # Perceptual hashing
python test_watermark.py                 # Hybrid DWT+DCT embed/extract
python test_forensic.py                  # Forensic report service
python test_security.py                  # Input validation, rate limiting
python test_security_attacks.py          # Attack scenarios
python test_robustness.py                # Watermark survival under transforms
python test_verification_accuracy.py     # End-to-end verdict accuracy
```

Endpoint and integration tests require the server running (`python run.py`):

```bash
python test_api.py                       # /api/v1/images/* round-trip
python test_endpoints.py                 # Status / health routes
python test_integration.py               # Full register → verify flow
python test_forensic_report_endpoint.py  # /api/v1/report/<id>
```

`demo_crypto.py` is a standalone walkthrough of the signing/verification flow with no server required.

---

## Documentation

- [README.md](README.md) — this file: overview, install, API surface
- [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) — architecture, verification flow, threat model, limitations
- [DEMO_GUIDE.md](DEMO_GUIDE.md) — demo scenarios and talking points

---

## Project Status

**Status**: Research Implementation Complete

**Suitable For**:
- ✅ Research and academic use
- ✅ Proof-of-concept demonstrations
- ✅ Educational purposes
- ✅ Internal provenance tracking

**NOT Suitable For**:
- ❌ Production deepfake detection
- ❌ Legal evidence (without expert validation)
- ❌ High-security adversarial environments
- ❌ Watermark-based verification (use cryptographic verification)

---

## License

Research Use Only

---

## Contributing

This is a research implementation. For production use, consider:
- Commercial watermarking SDKs (Digimarc, Vobile)
- C2PA implementation libraries
- Blockchain-based timestamping
- Hardware Security Modules (HSM) for key storage

---

## Acknowledgments

Built with:
- **Flask**: Web framework
- **cryptography**: Ed25519 signatures
- **OpenCV**: Image processing
- **PyWavelets**: DWT watermarking
- **SQLite**: Append-only registry

Inspired by:
- C2PA (Coalition for Content Provenance and Authenticity)
- Adobe Content Credentials
- Cryptographic provenance research

---

**PROVENA-FLASK**: Cryptographic provenance for AI images, with forensic watermarking as supplementary evidence.
