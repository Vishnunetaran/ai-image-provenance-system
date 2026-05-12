# PROVENA-FLASK Demo Guide

**Demonstrating Cryptographic Provenance & Forensic Verification**

This guide explains how to demonstrate the PROVENA-FLASK system in realistic scenarios that showcase both the strengths and honest limitations of cryptographic provenance tracking.

---

## Demo Philosophy

### Honest, Not Perfect

This demo is designed to be **forensically honest**, not to showcase perfect detection. The system demonstrates:

✅ **What Works**: Cryptographic signatures, append-only registry, perceptual hashing  
⚠️ **What's Supplementary**: Watermark extraction (may fail)  
❌ **What Doesn't Work**: Pixel-level integrity proof, timestamp verification  

---

## Two Demo Modes

### Mode A: Controlled Verification (Ideal Conditions)

**Scenario**: Register and verify the same file without modifications

**Purpose**: Demonstrate all verification layers working together

**Expected Results**:
- ✅ Cryptographic signature: VALID
- ✅ Perceptual hash: MATCH
- ✅ Watermark: EXTRACTED
- **Verdict**: VERIFIED

**Steps**:

1. **Register Image**
   ```
   - Open http://localhost:5000/
   - Upload image (drag-and-drop or click)
   - Click "Register Image"
   - Note the image_id returned
   ```

2. **Verify Same Image**
   ```
   - Upload the SAME watermarked image (from registration response)
   - Click "Verify Image"
   - Observe all three verification layers succeed
   ```

3. **View Forensic Report**
   ```
   - Click "View Forensic Report"
   - Review evidence summary
   - Note all verification methods succeeded
   ```

**Key Talking Points**:
- Cryptographic signature provides authoritative proof
- Perceptual hash confirms image similarity
- Watermark provides supplementary forensic trace
- All layers agree: image is verified

---

### Mode B: Realistic Verification (Real-World Conditions)

**Scenario**: Register image, then verify after JPEG compression, resizing, or format conversion

**Purpose**: Show that cryptographic proof remains valid even when the image has been transformed in transit. With the v2.0 hybrid DWT+DCT watermark, light transforms (JPEG Q≥75, ±10% resize, PNG↔JPEG) often still yield a successful watermark extraction; heavier transforms will lose it.

**Expected Results**:
- ✅ Cryptographic signature: VALID (metadata unchanged)
- ✅ Perceptual hash: MATCH (tolerant to compression/resize)
- ⚠️ Watermark: EXTRACTED for light transforms, LOST for heavy ones (Q<50, cropping, rotation, heavy filtering)
- **Verdict**: `verified` if watermark survived, `verified_modified` if it didn't — both are valid outcomes

**Steps**:

1. **Register Image**
   ```
   - Upload image
   - Click "Register Image"
   - Download the watermarked image
   - Save image_id for later
   ```

2. **Transform Image (Choose One)**
   
   **Option A: JPEG Compression**
   ```
   - Open watermarked image in image editor (GIMP, Photoshop, Paint.NET)
   - Save as JPEG with quality 75%
   - This simulates real-world image sharing (social media, email)
   ```
   
   **Option B: Resizing**
   ```
   - Resize watermarked image to 90% of original size
   - This simulates thumbnail generation or display resizing
   ```
   
   **Option C: Format Conversion**
   ```
   - Convert PNG → JPEG → PNG
   - This simulates format changes during sharing
   ```

3. **Verify Transformed Image**
   ```
   - Upload the transformed image
   - Click "Verify Image"
   - Observe:
     • Signature: VALID (metadata unchanged)
     • Perceptual hash: MATCH (tolerant to compression)
     • Watermark: may be EXTRACTED for light transforms,
       NOT EXTRACTED if the transform was aggressive
   ```

4. **Interpret Results**
   ```
   - Verdict: "verified" if watermark survived,
     "verified_modified" if it didn't
   - In both cases the cryptographic proof is intact
   - Watermark loss is EXPECTED and DOCUMENTED behavior
   ```

**Key Talking Points**:
- **Watermark failure does NOT invalidate provenance**
- Cryptographic signature remains authoritative
- Perceptual hash provides robust matching
- System honestly reports watermark extraction failure
- This demonstrates realistic forensic verification

---

## Demo Scenarios

### Scenario 1: Authentic AI-Generated Image

**Setup**: Register a legitimate AI-generated image

**Verification**: Verify the same image (Mode A)

**Expected Verdict**: VERIFIED

**Talking Points**:
- Complete provenance chain established
- All verification layers succeed
- Cryptographic proof of origin

---

### Scenario 2: Downloaded/Re-uploaded Image

**Setup**: Register image, download, compress to JPEG Q=75, re-upload

**Verification**: Verify compressed image (Mode B)

**Expected Verdict**: `verified` (if watermark survived) or `verified_modified` (if it was lost)

**Talking Points**:
- Cryptographic signature still valid
- Perceptual hash matches despite compression
- v2.0 watermark often survives Q=75 — if it doesn't, that's expected and documented
- **Provenance remains valid either way**

---

### Scenario 3: Tampered Metadata

**Setup**: Register image, manually modify metadata in database

**Verification**: Verify image with tampered metadata

**Expected Verdict**: TAMPERED

**Talking Points**:
- Cryptographic signature verification fails
- Tampering detected immediately
- Append-only registry prevents this in practice

---

### Scenario 4: Unknown Image

**Setup**: Upload image that was never registered

**Verification**: Verify unregistered image

**Expected Verdict**: NOT_FOUND

**Talking Points**:
- No provenance record exists
- System correctly identifies unknown image
- No false positives

---

## API Demo (Advanced)

### Using curl or Postman

#### Register Image
```bash
# Prepare base64-encoded image
base64 image.png > image_b64.txt

# Register
curl -X POST http://localhost:5000/api/v1/images/register \
  -H "Content-Type: application/json" \
  -d '{
    "image": "<base64_image_data>",
    "model_id": "gpt-vision-v1",
    "timestamp": "2026-01-26T19:00:00Z"
  }'
```

#### Verify Image
```bash
curl -X POST http://localhost:5000/api/v1/images/verify \
  -H "Content-Type: application/json" \
  -d '{
    "image": "<base64_image_data>"
  }'
```

#### Get Forensic Report
```bash
curl http://localhost:5000/api/v1/report/{image_id}?format=text
```

---

## Explaining the Verdict System

Two verdict vocabularies exist because two different endpoints answer two different questions.

### `POST /api/v1/images/verify` — what happened during this verification

| Verdict | Meaning |
|---------|---------|
| `verified` | Signature valid, perceptual hash matched, watermark extracted |
| `verified_modified` | Signature valid, perceptual hash matched, watermark lost (expected after some transforms) |
| `tampered` | Signature invalid or perceptual hash mismatch |
| `not_found` | No provenance record matches this image |

### `GET /api/v1/report/<image_id>` — forensic summary

| Verdict | Trigger |
|---------|---------|
| `authentic` | Confidence = 1.0 (all three layers passed) |
| `likely_authentic` | Confidence ≥ 0.5 with valid signature |
| `suspicious` | Mixed signals, low confidence, signature valid |
| `tampered` | Signature invalid |
| `not_found` | No provenance record |

### Confidence Scoring

**Confidence Score**: 0.0 - 1.0

**Calculation** (additive):
- Signature valid: +0.5
- Watermark extracted: +0.3
- Perceptual hash match: +0.2

**Interpretation**:
- 1.0: All layers passed (`authentic`)
- 0.5–0.9: Cryptographic proof valid, partial forensic evidence (`likely_authentic`)
- < 0.5: No cryptographic proof or contradictory signals

---

## Common Questions & Answers

### Q: Why does watermark extraction sometimes fail after JPEG compression?

**A**: JPEG compression uses lossy DCT quantization that attacks the same mid-frequency coefficients where watermarks live. The v2.0 hybrid DWT+DCT engine adds redundancy and a sync prefix that helps survive Q≥75, but at lower qualities the signal is destroyed. The system compensates by relying primarily on cryptographic signatures, which remain valid regardless.

### Q: Does watermark failure mean the image is not verified?

**A**: No. **Cryptographic signature is the authoritative proof**. Watermark is supplementary forensic evidence. If signature is valid, provenance is established even if watermark is lost.

### Q: Can this system detect deepfakes?

**A**: No. This system tracks **provenance** (where image came from), not **authenticity** (whether image is real). It proves an image was registered with specific metadata, not that the image content is genuine.

### Q: How is this different from watermarking products?

**A**: This is a **cryptographic provenance system**, not a watermarking product. Watermarks are supplementary forensic traces. The core proof comes from Ed25519 digital signatures and append-only registry.

### Q: What happens if the private key is compromised?

**A**: If the private key is compromised, an attacker can forge signatures for fake images. This is a **single point of failure**. Production systems should use Hardware Security Modules (HSM) and key rotation.

### Q: Can timestamps be trusted?

**A**: Timestamps are **self-reported** by the AI model provider, not independently verified. Trust relies on the reputation of the provider. Future: blockchain timestamping for independent verification.

---

## Demo Best Practices

### Do's ✅

- **Be honest** about limitations
- **Demonstrate realistic scenarios** (Mode B)
- **Explain cryptographic vs forensic evidence**
- **Show forensic reports** for transparency
- **Discuss industry alignment** (C2PA, Adobe)

### Don'ts ❌

- **Don't claim perfect detection**
- **Don't position as watermarking product**
- **Don't hide watermark extraction failures**
- **Don't promise pixel-level integrity**
- **Don't oversell capabilities**

---

## Presentation Outline

### 1. Introduction (2 min)
- What is PROVENA-FLASK?
- Cryptographic provenance, not watermarking
- Industry alignment (C2PA, Adobe)

### 2. Architecture Overview (3 min)
- Tier 1: Cryptographic signatures (authoritative)
- Tier 2: Perceptual hashing (robust)
- Tier 3: Watermarking (supplementary)

### 3. Demo Mode A: Ideal Conditions (5 min)
- Register image
- Verify same image
- All layers succeed
- View forensic report

### 4. Demo Mode B: Realistic Conditions (5 min)
- Register image
- Compress to JPEG (try Q=75 and Q=40 for contrast)
- Verify compressed image
- Watermark may survive at Q=75, will fail at Q=40
- Cryptographic proof remains valid in both cases

### 5. System Limitations (3 min)
- Watermark robustness
- Timestamp trust
- Pixel-level integrity
- Centralized trust

### 6. Q&A (5 min)

**Total**: ~20-25 minutes

---

## Conclusion

PROVENA-FLASK demonstrates **cryptographic provenance tracking** with honest limitations. The system is suitable for research and education, showcasing how cryptographic signatures provide authoritative proof of origin, with watermarking as supplementary forensic evidence.

**Key Takeaway**: Cryptographic provenance is robust and reliable. Watermark extraction is best-effort and may fail under heavy transformation. This is expected, documented, and does not invalidate the provenance proof.
