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

**Scenario**: Register image, then verify after JPEG compression or resizing

**Purpose**: Demonstrate realistic forensic verification where watermark may fail but cryptographic proof remains valid

**Expected Results**:
- ✅ Cryptographic signature: VALID (if metadata unchanged)
- ✅ Perceptual hash: MATCH (tolerant to modifications)
- ❌ Watermark: NOT EXTRACTED (expected failure)
- **Verdict**: VERIFIED_MODIFIED

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
     • Watermark: NOT EXTRACTED (expected)
   ```

4. **Interpret Results**
   ```
   - Verdict: VERIFIED_MODIFIED
   - Explanation: Cryptographic proof valid, watermark lost
   - This is EXPECTED and DOCUMENTED behavior
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

**Expected Verdict**: VERIFIED_MODIFIED

**Talking Points**:
- Cryptographic signature still valid
- Perceptual hash matches despite compression
- Watermark lost (expected, documented)
- **Provenance remains valid**

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

### 5-Level Verdict Scale

| Verdict | Meaning | Cryptographic Proof | Forensic Evidence |
|---------|---------|---------------------|-------------------|
| **verified** | All verification layers succeeded | ✅ Signature valid | ✅ Watermark extracted |
| **likely_authentic** | Cryptographic proof valid, some forensic evidence | ✅ Signature valid | ⚠️ Partial evidence |
| **verified_modified** | Cryptographic proof valid, watermark lost | ✅ Signature valid | ❌ Watermark lost |
| **suspicious** | Forensic evidence conflicts | ⚠️ Signature issues | ⚠️ Evidence conflicts |
| **tampered** | Cryptographic proof failed | ❌ Signature invalid | N/A |
| **not_found** | No provenance record | N/A | N/A |

### Confidence Scoring

**Confidence Score**: 0.0 - 1.0

**Calculation**:
- Signature valid: +0.5
- Perceptual hash match: +0.3
- Watermark extracted: +0.2

**Interpretation**:
- 0.8-1.0: High confidence (all layers agree)
- 0.5-0.8: Moderate confidence (cryptographic proof valid)
- 0.0-0.5: Low confidence (no cryptographic proof)

---

## Common Questions & Answers

### Q: Why does watermark extraction fail after JPEG compression?

**A**: JPEG compression uses lossy DCT quantization that destroys mid-frequency coefficients where watermarks are embedded. This is a **known limitation** of frequency-domain watermarking. The system compensates by relying primarily on cryptographic signatures, which remain valid.

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
- Compress to JPEG Q=75
- Verify compressed image
- Watermark fails (expected)
- Cryptographic proof remains valid

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

**Key Takeaway**: Cryptographic provenance is robust and reliable. Watermark extraction is probabilistic and may fail. This is expected, documented, and does not invalidate the provenance proof.

---

**Demo Guide Version**: 1.0  
**Last Updated**: 2026-01-26
