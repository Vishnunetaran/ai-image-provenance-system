# PROVENA: Technical Documentation

**Status**: Research Implementation

---

## Executive Summary

PROVENA is a provenance tracking system for AI-generated images. It provides **cryptographic proof of metadata integrity** through Ed25519 digital signatures, supplemented by perceptual hashing and a hybrid DWT+DCT invisible watermark (v2.0).

**What This System Proves:**
- An image was registered with specific metadata (model, timestamp)
- The metadata has not been tampered with (cryptographic signature)
- An image is perceptually similar to a registered image (perceptual hash)

**What This System Does NOT Prove:**
- Image pixels are unmodified (watermarks are unreliable)
- Timestamp is accurate (self-reported, not independently verified)
- Image is authentic (only that it was registered)

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Application                        │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Flask API Layer                          │
│  ┌──────────────┬──────────────┬──────────────┬──────────────┐  │
│  │   Register   │    Verify    │  Provenance  │    Report    │  │
│  │  POST /api/  │  POST /api/  │  GET /api/   │  GET /api/   │
│  │  v1/images/  │  v1/images/  │  v1/prov/    │  v1/report/  │
│  │  register    │  verify      │  {id}        │  {id}        │
│  └──────────────┴──────────────┴──────────────┴──────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Security Layer                              │
│  • Rate Limiting (60 req/min)                                    │
│  • Input Validation (SQL injection prevention)                   │
│  • Audit Logging (structured JSON)                               │
│  • Request Duration Tracking                                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer                               │
│  ┌────────────┬────────────┬────────────┬────────────────────┐  │
│  │   Crypto   │ Watermark  │  Percept.  │    Registry        │  │
│  │  Service   │  Service   │   Hash     │    Service         │  │
│  │            │            │  Service   │                    │  │
│  │  Ed25519   │    DWT     │   pHash    │  SQLite            │  │
│  │  Sign/     │  Embed/    │  dHash     │  Append-Only       │  │
│  │  Verify    │  Extract   │  aHash     │  Database          │  │
│  └────────────┴────────────┴────────────┴────────────────────┘  │
│  ┌────────────┬────────────────────────────────────────────────┐│
│  │ Forensic   │          Key Storage                           ││
│  │  Report    │          (File-based PEM)                      ││
│  │  Service   │                                                ││
│  └────────────┴────────────────────────────────────────────────┘│
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Data Layer                                 │
│  ┌──────────────────────┬──────────────────────────────────┐    │
│  │  Provenance DB       │  Audit Log DB                    │    │
│  │  (SQLite)            │  (SQLite)                        │    │
│  │  • Images            │  • Registry writes               │    │
│  │  • Signatures        │  • Verifications                 │    │
│  │  • Perceptual hashes │  • API access                    │    │
│  │  • Metadata          │  • Security events               │    │
│  └──────────────────────┴──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Technology Stack

- **Framework**: Flask 3.0
- **Cryptography**: Ed25519 via `cryptography` 41.x
- **Watermarking**: Hybrid DWT (`PyWavelets`) + DCT (`OpenCV`), embedded on the luminance channel
- **Image Processing**: OpenCV (`cv2`), Pillow
- **Perceptual Hashing**: DCT-based pHash (with `imagehash` available as a dependency)
- **Database**: SQLite with append-only triggers
- **Logging**: Python `logging` with structured (JSON-friendly) formatting

### API Surface

Implemented routes:

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/v1/images/register` | Sign metadata, embed watermark, write registry row |
| `POST` | `/api/v1/images/verify` | Try watermark, fall back to pHash, verify signature |
| `GET` | `/api/v1/provenance/<image_id>` | Return raw provenance record |
| `GET` | `/api/v1/report/<image_id>` | Forensic report; `?format=text` for plain text |
| `GET` | `/reports/<image_id>` | Same report under a separate blueprint |
| `GET` | `/health`, `/api/v1/status`, `/registry/status`, `/watermark/status`, `/verification/status`, `/reports/status` | Liveness |
| `GET` | `/` | Demo HTML UI |

Scaffolded but unimplemented (return HTTP 501): `GET /registry/records`, `POST /watermark/embed`, `POST /watermark/extract`, `POST /verification/check`. The corresponding logic is performed inside `/api/v1/images/register` and `/api/v1/images/verify` and is not exposed as standalone endpoints.

---

## Verification Flow

### Registration Flow

```
1. Client submits image + metadata
   ↓
2. API validates input (size, format, required fields)
   ↓
3. Generate unique image_id
   ↓
4. Compute perceptual hash (pHash) of original image
   ↓
5. Load/generate Ed25519 key pair
   ↓
6. Create metadata object:
   {
     image_id, model_id, timestamp, perceptual_hash
   }
   ↓
7. Sign metadata with private key → signature
   ↓
8. Embed watermark in image (hybrid DWT+DCT, v2.0):
   payload = image_id + ":" + timestamp, padded to 16 bytes (128 bits)
   ↓
9. Store in registry:
   - image_id
   - model_id
   - timestamp
   - watermark_payload
   - perceptual_hash
   - signature
   - public_key
   - key_id
   ↓
10. Log audit trail
   ↓
11. Return to client:
    - watermarked_image
    - signature
    - public_key
    - perceptual_hash
```

### Verification Flow

```
1. Client submits image for verification
   ↓
2. API validates input
   ↓
3. ATTEMPT 1: Extract watermark
   ├─ Success → extract image_id
   └─ Failure → image_id = None
   ↓
4. Compute current perceptual hash
   ↓
5. ATTEMPT 2: Find provenance record
   ├─ If image_id extracted → query by image_id
   ├─ If not found → search by perceptual_hash
   └─ If still not found → return NOT_FOUND
   ↓
6. Verify cryptographic signature:
   metadata = {image_id, model_id, timestamp, perceptual_hash}
   signature_valid = verify(metadata, signature, public_key)
   ↓
7. Check perceptual hash match:
   distance = hamming_distance(current_hash, stored_hash)
   perceptual_match = (distance ≤ 30)
   ↓
8. Determine verdict:
   ├─ watermark_extracted AND signature_valid AND perceptual_match
   │  → VERIFIED
   ├─ signature_valid AND perceptual_match (no watermark)
   │  → VERIFIED_MODIFIED
   ├─ signature_invalid
   │  → TAMPERED
   └─ not found
      → NOT_FOUND
   ↓
9. Log audit trail
   ↓
10. Return verification result
```

### Multi-Layer Verification Strategy

The system uses **defense in depth** with three verification layers:

| Layer | Method | Robustness | Purpose |
|-------|--------|------------|---------|
| **Primary** | Ed25519 Signature | ✅ ROBUST | Cryptographic proof of metadata integrity |
| **Secondary** | Perceptual Hash (pHash) | ✅ ROBUST | Tolerant matching despite modifications |
| **Tertiary** | Hybrid DWT+DCT Watermark (v2.0) | ⚠️ BEST-EFFORT | Supplementary evidence; survives light transforms |

**Rationale**: Watermarks are best-effort forensic traces. The system always treats the Ed25519 signature plus perceptual-hash match as authoritative — a missing watermark produces the `verified_modified` verdict, not a failure.

### Verdict scales

Two verdict vocabularies live side by side and serve different surfaces:

| Surface | Possible verdicts |
|---------|-------------------|
| `POST /api/v1/images/verify` (`status` field) | `verified`, `verified_modified`, `tampered`, `not_found` |
| `GET /api/v1/report/<image_id>` (`verdict` field) | `authentic`, `likely_authentic`, `suspicious`, `tampered`, `not_found` |

The forensic report also returns a `confidence_score` in `[0.0, 1.0]`: +0.5 if the signature is valid, +0.3 if the watermark was extracted, +0.2 if the perceptual hash matched. `authentic` requires 1.0; `likely_authentic` requires ≥0.5 with a valid signature.

---

## Provenance vs Watermark: Critical Distinction

### Provenance (What We Track)

**Provenance** = metadata about image creation:
- Model ID (e.g., "gpt-vision-v1")
- Timestamp (e.g., "2026-01-26T15:00:00Z")
- Prompt hash (optional)
- Cryptographic signature

**What Provenance Proves:**
✅ Metadata was signed by holder of private key  
✅ Metadata has not been tampered with  
✅ Image was registered at a specific time  

**What Provenance Does NOT Prove:**
❌ Image pixels are unmodified  
❌ Timestamp is accurate (self-reported)  
❌ Image is "authentic" or "real"  

### Watermark (How We Embed)

**Watermark** = invisible data embedded in image pixels:
- Payload: image_id + timestamp (16 bytes)
- Method: DWT (Discrete Wavelet Transform)
- Embedding: Modify wavelet coefficients

**What Watermarks Prove:**
✅ Image contains embedded data (if extractable)  
✅ Image likely originated from this system  

**What Watermarks Do NOT Prove:**
❌ Image is unmodified (watermark can be removed)  
❌ Metadata is accurate (watermark can be forged)  
❌ Image is authentic  

### Key Insight

**Provenance ≠ Authenticity**

This system provides **cryptographic proof that metadata was signed**, not that the image is "real" or "unmodified". An AI-generated image can have valid provenance but still be:
- Edited after generation
- Re-generated from same prompt
- Completely fabricated

---

## Honest System Limitations

### 1. Watermark Robustness

**Limitation**: Watermark extraction is best-effort and can still fail under aggressive transformations.

**v2.0 (current) engine**: hybrid DWT+DCT on the luminance (Y) channel, with:
- 5× redundant bit embedding and majority voting
- 16-bit synchronization prefix for limited geometric resync
- Repetition coding so partial bit loss is tolerable
- Embedding strength tuned for PSNR ≥45 dB / SSIM ≥0.99

**Designed to survive**: JPEG Q≥75, ±10% resizing, basic format conversion (PNG↔JPEG).

**Will likely fail under**: extreme compression (Q<50), cropping, rotation, heavy filtering, or any geometric attack outside the sync window.

**Impact**: Watermark is a *supplementary* signal. Even when extraction fails, the cryptographic signature plus perceptual-hash match still produce a `verified_modified` verdict — this is the intended behavior, not an error.

**Mitigation**: System always relies on cryptographic signatures (robust) and perceptual hashing (tolerant) for authoritative proof.

### 2. Timestamp Trust

**Limitation**: Timestamps are self-reported, not independently verified.

**What This Means**:
- AI model provider can lie about generation time
- No blockchain or trusted timestamping authority
- Timestamp is only as trustworthy as the signer

**Impact**: Cannot prove when image was actually generated.

**Mitigation**: Trust model relies on reputation of AI model provider.

### 3. Pixel-Level Integrity

**Limitation**: System does not prove image pixels are unmodified.

**What We Prove**:
✅ Metadata integrity (signature)  
✅ Perceptual similarity (pHash)  

**What We Don't Prove**:
❌ Pixel-by-pixel identity  
❌ No post-processing  
❌ No content manipulation  

**Impact**: Verified image may have been edited after generation.

**Mitigation**: Perceptual hash catches major changes; minor edits are undetectable.

### 4. Key Security

**Limitation**: Security depends entirely on private key protection.

**Threat**: If private key is compromised:
- Attacker can forge signatures
- Attacker can create fake provenance
- All past signatures become untrustworthy

**Impact**: Single point of failure.

**Mitigation**: 
- Secure key storage (file permissions)
- Key rotation support
- Audit logging

### 5. Perceptual Hash Limitations

**Limitation**: Perceptual hashes can be fooled.

**What pHash Detects**:
✅ Brightness adjustments  
✅ Minor compression  
✅ Slight resizing  

**What pHash Misses**:
❌ Content-aware edits  
❌ Object removal/addition  
❌ Sophisticated manipulations  

**Impact**: Perceptual match does not guarantee identical content.

### 6. No Blockchain

**Limitation**: Registry is centralized, not distributed.

**What This Means**:
- Single database can be compromised
- No decentralized consensus
- Append-only is enforced by code, not cryptography

**Impact**: Trust in system operator required.

**Future**: Could integrate with blockchain for decentralized timestamping.

---

## Threat Model

### Threats We Defend Against

| Threat | Defense | Effectiveness |
|--------|---------|---------------|
| **Signature Forgery** | Ed25519 cryptography | ✅ ROBUST (computationally infeasible) |
| **Metadata Tampering** | Digital signatures | ✅ ROBUST (detected immediately) |
| **Replay Attacks** | UNIQUE(model_id, timestamp) constraint | ✅ ROBUST (prevented by database) |
| **SQL Injection** | Input validation, parameterized queries | ✅ ROBUST |
| **Rate Limiting Bypass** | Per-IP tracking | ✅ MODERATE (can be circumvented with VPN) |

### Threats We Do NOT Defend Against

| Threat | Why Not | Impact |
|--------|---------|--------|
| **Aggressive watermark removal** | v2.0 improves resilience but is not adversarial-grade | ⚠️ MODERATE (signature still proves provenance) |
| **Private Key Compromise** | No HSM, file-based storage | ⚠️ CRITICAL (game over if compromised) |
| **Sophisticated Image Edits** | Perceptual hash limitations | ⚠️ MODERATE (undetectable edits possible) |
| **Timestamp Fraud** | Self-reported timestamps | ⚠️ MODERATE (no independent verification) |
| **Registry Compromise** | Centralized database | ⚠️ HIGH (single point of failure) |
| **Deepfake Detection** | Out of scope | ⚠️ N/A (not a deepfake detector) |

### Attack Scenarios

#### Scenario 1: Attacker Removes Watermark
1. Attacker obtains watermarked image
2. Applies heavy JPEG compression or filtering
3. Watermark is destroyed

**Result**: ✅ System still verifies via signature + perceptual hash  
**Verdict**: VERIFIED_MODIFIED (watermark lost but signature valid)

#### Scenario 2: Attacker Tampers with Metadata
1. Attacker modifies provenance record in database
2. Changes model_id or timestamp

**Result**: ✅ Signature verification fails  
**Verdict**: TAMPERED

#### Scenario 3: Attacker Steals Private Key
1. Attacker gains access to key storage
2. Forges signatures for fake images

**Result**: ❌ System cannot detect forgery  
**Verdict**: VERIFIED (false positive)  
**Mitigation**: Secure key storage, key rotation, audit logging

#### Scenario 4: Attacker Edits Image Content
1. Attacker removes object from image
2. Perceptual hash changes slightly

**Result**: ⚠️ Depends on edit severity  
**Verdict**: May still verify if perceptual distance ≤ 30

---

## Non-Goals

This system explicitly does NOT attempt to:

1. **Detect Deepfakes**: Not a deepfake detection system
2. **Prove Image Authenticity**: Only proves metadata was signed
3. **Prevent Image Editing**: Cannot stop post-processing
4. **Guarantee Pixel Integrity**: Does not prove pixels are unmodified
5. **Verify Timestamp Accuracy**: Timestamps are self-reported
6. **Replace Forensic Analysis**: Complements, not replaces, expert analysis
7. **Provide Legal Proof**: Not designed for courtroom admissibility
8. **Achieve Perfect Robustness**: Watermarks have known limitations

---

## Future Improvements

### Short-Term (3-6 months)

1. **Upgrade Watermarking**
   - Implement spread spectrum watermarking
   - Add error correction codes (BCH, Reed-Solomon)
   - Geometric transformation resistance
   - **Target**: ≥90% extraction success after transformations

2. **Enhanced Security**
   - Hardware Security Module (HSM) integration
   - Multi-signature support
   - Automated key rotation

3. **Blockchain Integration**
   - Decentralized timestamping (e.g., OpenTimestamps)
   - Immutable audit trail
   - Cross-chain verification

### Medium-Term (6-12 months)

4. **Advanced Verification**
   - Multi-model consensus (verify with multiple AI detectors)
   - Content-based verification (not just metadata)
   - Provenance chain tracking (image derivatives)

5. **Scalability**
   - Distributed database (PostgreSQL, MongoDB)
   - Redis-based rate limiting
   - Horizontal scaling

6. **Standards Compliance**
   - C2PA (Coalition for Content Provenance and Authenticity)
   - IPTC metadata standards
   - W3C Verifiable Credentials

### Long-Term (12+ months)

7. **Zero-Knowledge Proofs**
   - Prove provenance without revealing metadata
   - Privacy-preserving verification

8. **Federated Learning**
   - Collaborative deepfake detection
   - Distributed model training

9. **Legal Framework**
   - Courtroom admissibility research
   - Expert witness protocols
   - Chain of custody documentation

---

## Academic Defensibility

### Research Contributions

1. **Multi-Layer Verification**: Combining cryptography, perceptual hashing, and watermarking
2. **Honest Limitation Documentation**: Transparent about what system can/cannot do
3. **Append-Only Registry**: Tamper-evident provenance storage
4. **Forensic Reporting**: Confidence scoring and system limitation disclosure

### Known Limitations (Acknowledged)

- Watermark robustness improved in v2.0 but still bounded — adversarial removal and heavy compression can defeat extraction
- Centralized trust model (no blockchain)
- Self-reported timestamps (no independent verification)
- Basic threat model (sophisticated attacks not addressed)

### Suitable For

✅ Research prototypes  
✅ Proof-of-concept demonstrations  
✅ Educational purposes  
✅ Internal provenance tracking  

### NOT Suitable For

❌ Production deepfake detection  
❌ Legal evidence (without expert validation)  
❌ High-security applications  
❌ Adversarial environments  

---

## Industry Readiness

### Production Deployment Checklist

- [ ] Upgrade watermarking to commercial solution (Digimarc, Vobile)
- [ ] Implement HSM for key storage
- [ ] Add blockchain timestamping
- [ ] Conduct security audit
- [ ] Implement distributed database
- [ ] Add monitoring and alerting
- [ ] Create incident response plan
- [ ] Document legal compliance
- [ ] Train support staff
- [ ] Establish SLAs

### Current Status

**Research Implementation**: ✅ Complete  
**Production Ready**: ⚠️ Partial — cryptography is robust; watermarking is best-effort, key storage is file-based, and the registry is centralized.  
**Recommended Use**: Internal provenance tracking, research, education  

---

## Conclusion

PROVENA successfully demonstrates **cryptographic provenance tracking** for AI-generated images. The system provides robust metadata integrity through Ed25519 signatures, supplemented by perceptual hashing.

**Key Strengths**:
- Cryptographic signatures are robust and reliable
- Append-only registry prevents tampering
- Multi-layer verification provides defense in depth
- Honest limitation documentation builds trust

**Key Weaknesses**:
- Watermark extraction is best-effort — adversarial transforms can defeat it
- Centralized trust model
- No independent timestamp verification
- Limited threat model

**Bottom Line**: System is suitable for **research and internal use**, but requires significant upgrades (commercial watermarking, HSM, blockchain) for production deployment in adversarial environments.

---

**License**: Research Use Only
