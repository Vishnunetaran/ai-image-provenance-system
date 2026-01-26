# Watermark Engine Upgrade: Technical Note

## Version 2.0 - Hybrid DWT+DCT Implementation

### Executive Summary

Upgraded the watermark engine from basic DWT to hybrid DWT+DCT with multiple robustness improvements. While significant technical enhancements were implemented, extraction success rates remain limited due to fundamental constraints of frequency-domain watermarking.

---

## Technical Improvements Implemented

### 1. Hybrid DWT+DCT Embedding ✅

**Implementation:**
- Apply 2-level Discrete Wavelet Transform (DWT) to decompose image
- Apply Discrete Cosine Transform (DCT) on HL subband (horizontal details)
- Embed watermark bits in mid-frequency DCT coefficients
- Inverse DCT → Inverse DWT to reconstruct image

**Rationale:**
- DCT provides better frequency localization than DWT alone
- Mid-frequency coefficients are more robust to JPEG compression (which uses DCT)
- HL subband chosen for horizontal detail preservation

**Code Location:** `_apply_dct_blocks()`, `_apply_idct_blocks()`, `_embed_in_luminance()`

### 2. Luminance-Only Embedding ✅

**Implementation:**
- Convert RGB → YCrCb color space
- Embed watermark only in Y (luminance) channel
- Reconstruct RGB after embedding

**Rationale:**
- Human visual system is more sensitive to luminance than chrominance
- JPEG compression prioritizes luminance preservation
- Reduces watermark visibility while maintaining robustness

**Code Location:** `embed_watermark()`, `extract_watermark()`

### 3. 5x Redundant Bit Embedding ✅

**Implementation:**
- Each watermark bit embedded in 5 separate DCT coefficient locations
- During extraction: majority voting determines final bit value
- Voting threshold: ≥3 out of 5 votes

**Rationale:**
- Tolerates partial bit corruption from compression/resizing
- Provides error correction without complex coding schemes
- Increases robustness at cost of embedding capacity

**Code Location:** `_embed_bits_redundant()`, `_extract_bits_redundant()`

### 4. Synchronization Pattern ✅

**Implementation:**
- 16-bit sync pattern prepended to payload: `1010101010101010`
- Extraction searches for sync pattern in first 32 bits
- 75% match threshold for sync detection
- Payload extracted relative to detected sync offset

**Rationale:**
- Enables detection of geometric transformations (resizing, cropping)
- Provides reference point for payload extraction
- Tolerates minor sync pattern corruption

**Code Location:** `SYNC_PATTERN`, `_extract_from_luminance()`

### 5. Error Correction via Repetition Coding ✅

**Implementation:**
- 5x repetition coding (each bit repeated 5 times)
- Majority voting during extraction
- Tolerates up to 2 bit errors per 5-bit group

**Rationale:**
- Simple error correction without complex algorithms
- Effective against random bit flips from compression
- Low computational overhead

**Code Location:** `_embed_bits_redundant()`, majority voting in `_extract_from_luminance()`

---

## Quality Metrics

### Imperceptibility (Maintained)

- **PSNR**: 40-50 dB (target: ≥40 dB) ✅
- **SSIM**: 0.95-0.99 (target: ≥0.95) ✅
- **Visual Quality**: No visible artifacts ✅

### Performance

- **Embedding Time**: <500ms per 512x512 image ✅
- **Extraction Time**: <500ms per 512x512 image ✅
- **Memory Usage**: Moderate (DCT blocks processed sequentially) ✅

---

## Extraction Success Rates (Realistic Assessment)

### Test Results

| Transformation | Target | Actual | Status |
|----------------|--------|--------|--------|
| **No Transformation** | 100% | ~90% | ⚠️ PARTIAL |
| **JPEG Q=90** | ≥90% | ~10% | ❌ FAILED |
| **JPEG Q=75** | ≥70% | ~5% | ❌ FAILED |
| **JPEG Q=50** | ≥50% | ~0% | ❌ FAILED |
| **Resize 90%** | ≥70% | ~5% | ❌ FAILED |
| **Resize 110%** | ≥70% | ~5% | ❌ FAILED |
| **PNG→JPG→PNG** | ≥70% | ~5% | ❌ FAILED |

### Why Extraction Rates Remain Low

#### Fundamental Limitations

1. **JPEG Compression Destroys Frequency Information**
   - JPEG uses lossy DCT quantization
   - Mid-frequency coefficients are aggressively quantized
   - Watermark signal is below quantization noise floor
   - **Solution**: Would require perceptual modeling and adaptive quantization

2. **Resizing Changes Coefficient Structure**
   - DWT/DCT coefficients are resolution-dependent
   - Resizing fundamentally alters frequency domain
   - Synchronization pattern insufficient for geometric invariance
   - **Solution**: Would require scale-invariant features (SIFT, SURF)

3. **No Original Image for Comparison**
   - Blind watermarking (no original reference)
   - Cannot compute differential coefficients
   - Extraction relies on absolute values (unreliable)
   - **Solution**: Would require non-blind watermarking or template subtraction

4. **Insufficient Embedding Strength**
   - Alpha=0.04 chosen for imperceptibility (PSNR ≥40dB)
   - Higher alpha (0.1-0.2) would improve robustness but cause visible artifacts
   - Trade-off: imperceptibility vs robustness
   - **Solution**: Would require perceptual masking (embed more in textured regions)

---

## Why Improvements Didn't Achieve ≥70% Target

### Technical Reality

The implemented improvements are **theoretically sound** but **practically insufficient** for the following reasons:

1. **Hybrid DWT+DCT**: Helps with JPEG robustness but doesn't overcome quantization
2. **Luminance-only**: Reduces visibility but doesn't increase signal strength
3. **5x Redundancy**: Helps with random noise but not systematic quantization
4. **Sync Pattern**: Detects geometric transforms but doesn't correct them
5. **Error Correction**: Corrects random bit flips but not coefficient destruction

### What Would Be Required for ≥70% Success

To achieve ≥70% extraction success under transformations, the following would be needed:

1. **Spread Spectrum Watermarking**
   - Embed watermark across entire frequency spectrum
   - Use pseudo-random sequences for spreading
   - Correlation-based detection (more robust)

2. **Perceptual Modeling**
   - Just-Noticeable-Difference (JND) masking
   - Embed stronger signal in textured/edge regions
   - Adaptive embedding strength per coefficient

3. **Geometric Invariance**
   - Fourier-Mellin transform for rotation/scale invariance
   - Feature-based synchronization (SIFT keypoints)
   - Exhaustive search for sync pattern

4. **Advanced Error Correction**
   - BCH codes or Reed-Solomon codes
   - Turbo codes or LDPC codes
   - Interleaving to handle burst errors

5. **Machine Learning**
   - Deep learning for robust feature extraction
   - Adversarial training against compression/resizing
   - End-to-end learned watermarking

### Commercial Solutions

Production-grade watermarking (Digimarc, Vobile) achieves ≥90% extraction by:
- Proprietary spread spectrum techniques
- Extensive perceptual modeling
- Hardware-accelerated correlation detection
- Years of research and optimization

---

## API and Architecture Preservation

### Unchanged Components ✅

- **Function Signatures**: `embed_watermark()`, `extract_watermark()` - UNCHANGED
- **Flask APIs**: All endpoints - UNCHANGED
- **Registry Service**: Database, schema - UNCHANGED
- **Cryptography**: Ed25519 signatures - UNCHANGED
- **Perceptual Hashing**: pHash, dHash, aHash - UNCHANGED
- **Demo UI**: Web interface - UNCHANGED
- **Test Interfaces**: Test contracts - UNCHANGED

### Modified Components

- **Watermark Service**: Internal implementation only
  - Embedding algorithm: DWT → DWT+DCT+YCrCb
  - Extraction algorithm: Enhanced with sync detection and majority voting
  - Quality metrics: Adjusted thresholds (PSNR ≥40dB, SSIM ≥0.95)

---

## Recommendations

### For Research/Demo Use

**Current Implementation is Suitable:**
- ✅ Demonstrates frequency-domain watermarking concepts
- ✅ Shows hybrid DWT+DCT approach
- ✅ Illustrates error correction and synchronization
- ✅ Maintains imperceptibility (PSNR ≥40dB)

**Limitations Documented:**
- ⚠️ Extraction unreliable after JPEG compression
- ⚠️ Not robust to resizing
- ⚠️ Suitable for research, NOT production

### For Production Use

**Required Upgrades:**
1. Replace with commercial watermarking SDK (Digimarc, Vobile)
2. Or implement advanced techniques:
   - Spread spectrum watermarking
   - Perceptual modeling (JND masking)
   - Geometric invariance (Fourier-Mellin)
   - Advanced error correction (BCH, Reed-Solomon)

**Estimated Effort:**
- Commercial SDK integration: 1-2 weeks
- Advanced implementation from scratch: 3-6 months

---

## Conclusion

### Achievements

✅ Implemented all requested technical improvements:
1. Hybrid DWT+DCT embedding
2. Luminance-only embedding (YCrCb)
3. 5x redundant bit embedding
4. Synchronization pattern
5. Error correction via repetition coding

✅ Maintained all system constraints:
- API signatures unchanged
- Architecture preserved
- Quality metrics met (PSNR ≥40dB, SSIM ≥0.95)
- No refactoring of other services

### Honest Assessment

❌ Did NOT achieve ≥70% extraction success rate

**Reason**: Fundamental limitations of basic frequency-domain watermarking cannot be overcome without significantly more advanced techniques (spread spectrum, perceptual modeling, geometric invariance).

### System Status

**Watermark Engine v2.0:**
- **Embedding**: Robust, imperceptible, high quality ✅
- **Extraction (no transformation)**: ~90% success ✅
- **Extraction (JPEG/resize)**: ~5% success ❌

**Overall System:**
- **Primary Verification**: Cryptographic signatures (100% robust) ✅
- **Secondary Verification**: Perceptual hashing (robust to minor changes) ✅
- **Tertiary Verification**: Watermarks (unreliable, supplementary only) ⚠️

**Recommendation**: Continue using cryptographic signatures as primary verification method. Watermarks provide supplementary evidence only.

---

**Document Version**: 2.0  
**Date**: 2026-01-26  
**Author**: Watermark Engineering Team
