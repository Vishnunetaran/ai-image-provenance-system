# ANTIGRAVITY MASTER PROMPT
## For PROVENA Trinity Implementation

---

## HOW TO USE THIS PROMPT

**Step 1:** Open Antigravity  
**Step 2:** Upload these files as context:
- `PRD_PROVENA_TRINITY.docx`
- `task_breakdown.md`
- `ultimate_watermarking_solution.md`

**Step 3:** Copy and paste the relevant prompt below for your current phase

---

## PHASE 1: LATENT WATERMARKING

### Week 1-2: Initial Implementation

```
I'm implementing PROVENA Trinity, an AI image detection system with multi-layer watermarking.

CONTEXT:
- I have an existing watermarking system (PROVENA v1.0) with 5% detection rate
- I'm implementing latent-space watermarking (Tree-Ring method) to achieve 90%+ detection
- Working in branch: feature/trinity-watermarking

TASK:
Implement the latent watermarking component based on Tree-Ring research (Wen et al., 2024).

SPECIFICATIONS FROM PRD:
1. Embed watermark during diffusion process (not post-generation)
2. Inject keyed noise pattern at timesteps T/2, T/4, T/8
3. Use ChaCha20 for pattern generation
4. Payload: 128-bit UUID + 32-bit timestamp
5. Target: PSNR ≥42dB, SSIM ≥0.98

FILES TO CREATE:
- src/watermarking/latent/pattern_generator.py
- src/watermarking/latent/payload.py
- src/watermarking/latent/diffusion_injector.py
- src/watermarking/latent/extractor.py
- tests/test_latent.py

REQUIREMENTS:
- Use PyTorch and Hugging Face diffusers library
- Integrate with Stable Diffusion pipeline
- Follow existing PROVENA code style
- Add comprehensive docstrings and type hints
- Write unit tests for each function

START WITH: pattern_generator.py - implement ChaCha20 keyed pattern generation
```

---

### Week 3-4: Testing & Optimization

```
CONTEXT:
I have basic latent watermarking working. Now I need to test robustness and optimize performance.

CURRENT STATUS:
- Embedding works: Watermark injected during diffusion
- Extraction works: Can detect watermark on clean images
- Accuracy: 95% on clean images

TASK:
Create comprehensive test suite and optimize for JPEG compression robustness.

TEST SCENARIOS:
1. JPEG compression (Q=10, 30, 50, 75, 90)
2. Resizing (0.25x, 0.5x, 0.75x, 1.5x, 2x)
3. Screenshot simulation (blur + chroma subsampling)
4. Cropping (10%, 20%, 30%)
5. Color adjustments (brightness, contrast, saturation)

CREATE:
- tests/robustness/test_transforms.py
- benchmarks/latent_watermark_benchmark.py
- scripts/generate_benchmark_report.py

OUTPUT:
Benchmark report (Markdown) with:
- Table of detection rates for each transform
- Graphs showing degradation curves
- Failure case analysis
- Recommendations for improvement

TARGET METRICS (from PRD):
- Clean images: 99% detection
- JPEG Q=50: 85% detection
- Screenshot: 80% detection
```

---

## PHASE 2: PIXEL WATERMARKING

### Week 11-12: Encoder-Decoder Implementation

```
CONTEXT:
Implementing learned pixel-space watermarking with adversarial training.

ARCHITECTURE (from PRD):
- Encoder: CNN+ViT hybrid (ResNet-50 + ViT-Small)
- Decoder: CNN+ViT hybrid (mirror encoder)
- Training: Joint end-to-end optimization

TASK:
Implement encoder-decoder models and training pipeline.

SPECIFICATIONS:
1. Encoder takes image + watermark payload → outputs watermarked image
2. Decoder takes image → extracts watermark payload
3. Loss functions:
   - Watermark accuracy: Binary cross-entropy on bits
   - Perceptual quality: LPIPS + MSE
   - Combined: λ_accuracy * L_accuracy + λ_quality * L_quality

FILES TO CREATE:
- src/watermarking/pixel/encoder.py
- src/watermarking/pixel/decoder.py
- src/watermarking/pixel/train.py
- configs/encoder_decoder_config.yaml

TRAINING HYPERPARAMETERS:
- Batch size: 32
- Learning rate: 1e-4 (AdamW)
- Epochs: 50 (clean images first)
- Augmentations: Random crop, horizontal flip (mild)

DATASET:
- Train: 40K AI images + 40K natural images
- Val: 5K + 5K
- Test: 5K + 5K

START WITH: encoder.py - implement CNN+ViT hybrid architecture
Use timm library for pretrained weights.
```

---

### Week 13-14: Adversarial Training

```
CONTEXT:
Adding adversarial training to make watermark resistant to removal attacks.

CURRENT STATUS:
- Encoder-decoder works on clean images (99% accuracy)
- Need to make it robust against attacks

TASK:
Implement adversarial training loop with removal network.

ARCHITECTURE:
1. Encoder: Embeds watermark
2. Attacker: U-Net that tries to remove watermark
3. Decoder: Extracts watermark from attacked image

TRAINING LOOP (min-max):
for each batch:
    # Encoder embeds watermark
    watermarked = encoder(image, watermark)
    
    # Attacker tries to remove
    attacked = attacker(watermarked)
    
    # Decoder tries to extract
    extracted = decoder(attacked)
    
    # Update networks
    encoder_decoder_loss = accuracy(extracted, watermark) + quality(attacked, image)
    attacker_loss = -accuracy(extracted, watermark) + similarity(attacked, watermarked)
    
    update encoder, decoder (minimize their loss)
    update attacker (minimize its loss = maximize removal)

AUGMENTATIONS (heavy):
- JPEG compression Q=10-95
- Resize 0.25x-2x
- Gaussian blur σ=0.5-2.0
- Salt & pepper noise
- Color jitter (brightness, contrast, saturation)

FILES:
- src/watermarking/pixel/attacker.py
- src/watermarking/pixel/adversarial_train.py

TARGET:
95% watermark detection even after attacker + JPEG Q=50
```

---

## PHASE 3: MULTI-LAYER DETECTION

### Week 15-16: Ensemble Implementation

```
CONTEXT:
Combining all detection layers into ensemble system.

COMPONENTS:
1. Latent watermark decoder (primary)
2. Perceptual hash (fast fallback)
3. CNN style classifier (EfficientNet-B4)
4. ViT global detector (ViT-Small)

TASK:
Implement 4-layer ensemble with weighted voting.

DETECTION LOGIC:
def detect(image):
    # Layer 1: Try watermark extraction
    watermark, wm_confidence = latent_decoder(image)
    if wm_confidence > 0.9:
        return VERIFIED, watermark
    
    # Layer 2: Perceptual hash lookup
    phash = compute_phash(image)
    matches = registry.search(phash, threshold=15)
    if matches:
        return VERIFIED, matches[0]
    
    # Layer 3: CNN classifier
    cnn_score = cnn_classifier(image)
    
    # Layer 4: ViT detector
    vit_score = vit_detector(image)
    
    # Ensemble voting
    combined = 0.4 * wm_confidence + 0.3 * phash_confidence + 0.2 * cnn_score + 0.1 * vit_score
    
    if combined > 0.8:
        return LIKELY_VERIFIED
    elif combined > 0.6:
        return SUSPICIOUS
    else:
        return NOT_DETECTED

FILES:
- src/detection/ensemble.py
- src/detection/cnn_classifier.py
- src/detection/vit_detector.py
- api/v2/detect.py (updated endpoint)

TESTING:
Comprehensive test on 10K images with 200+ transform combinations.
Target: 99% detection at JPEG Q=50
```

---

## DEBUGGING PROMPTS

### When Watermark Extraction Fails

```
PROBLEM:
Watermark extraction failing after JPEG compression.
Current detection rate: 40% at Q=50 (target: 85%)

DEBUG STEPS:
1. Visualize embedded watermark in frequency domain
2. Check if JPEG is destroying specific frequency bands
3. Try multi-scale embedding (LL+HL+HH bands instead of just HL)
4. Increase LDPC redundancy (more parity bits)

TASK:
Analyze failure modes and propose fixes.

FILES TO EXAMINE:
- src/watermarking/latent/diffusion_injector.py
- src/watermarking/latent/extractor.py

CREATE:
- debug/visualize_watermark.py (plot frequency domain)
- debug/analyze_jpeg_impact.py (show which coefficients destroyed)

SUGGEST:
Specific code changes to improve robustness
```

---

### When Training Isn't Converging

```
PROBLEM:
Encoder-decoder training stuck at 70% accuracy (target: 99%)

DEBUG INFO:
- Loss plateaus after epoch 10
- Validation accuracy not improving
- PSNR looks good (42dB)

POTENTIAL CAUSES:
1. Learning rate too high/low
2. Architecture mismatch (encoder/decoder not compatible)
3. Loss function weighting incorrect
4. Gradient vanishing/exploding
5. Data augmentation too aggressive

TASK:
Diagnose issue and fix training.

STEPS:
1. Add gradient norm logging
2. Visualize encoder output (is watermark visible?)
3. Try different learning rates (1e-3, 1e-4, 1e-5)
4. Check if decoder architecture mirrors encoder
5. Adjust loss weights (try λ_accuracy=0.9, λ_quality=0.1)

OUTPUT:
Training diagnostic report + fixes
```

---

## CODE STYLE GUIDELINES

**For all implementations:**

```python
# Good: Type hints + docstrings
def generate_pattern(
    image_id: str,
    secret_key: bytes,
    shape: Tuple[int, int, int]
) -> torch.Tensor:
    """Generate keyed noise pattern using ChaCha20.
    
    Args:
        image_id: Unique identifier for the image
        secret_key: 32-byte secret key for ChaCha20
        shape: Desired pattern shape (C, H, W)
    
    Returns:
        Noise pattern tensor of shape `shape`
        
    Example:
        >>> pattern = generate_pattern("img-123", key, (3, 512, 512))
        >>> pattern.shape
        torch.Size([3, 512, 512])
    """
    ...

# Good: Comprehensive error handling
try:
    watermark = extract_watermark(image)
except ExtractionError as e:
    logger.warning(f"Watermark extraction failed: {e}")
    return None, 0.0  # Return None with 0 confidence

# Good: Informative logging
logger.info(f"Training epoch {epoch}/{total_epochs}")
logger.info(f"  Accuracy: {accuracy:.2%}")
logger.info(f"  PSNR: {psnr:.2f}dB")
logger.info(f"  Loss: {loss:.4f}")

# Good: Unit tests for each function
def test_pattern_generation():
    """Test that patterns are deterministic given same inputs."""
    key = b"0" * 32
    pattern1 = generate_pattern("img-123", key, (3, 64, 64))
    pattern2 = generate_pattern("img-123", key, (3, 64, 64))
    assert torch.allclose(pattern1, pattern2)
    
def test_pattern_uniqueness():
    """Test that different IDs produce different patterns."""
    key = b"0" * 32
    pattern1 = generate_pattern("img-123", key, (3, 64, 64))
    pattern2 = generate_pattern("img-456", key, (3, 64, 64))
    assert not torch.allclose(pattern1, pattern2)
```

---

## FINAL INTEGRATION PROMPT

```
CONTEXT:
All components complete. Final integration into PROVENA API.

COMPONENTS:
✅ Latent watermarking (90% detection)
✅ Pixel watermarking (95% detection)
✅ CNN classifier (85% detection)
✅ ViT detector (80% detection)
✅ Ensemble logic (99% combined)

TASK:
Update API endpoints and deploy.

API CHANGES:

POST /api/v2/images/register
{
  "image": "base64...",
  "model_id": "stable-diffusion-v3",
  "enable_latent": true,
  "enable_pixel": true
}

Response:
{
  "image_id": "img-uuid",
  "watermarked_image": "base64...",
  "latent_embedded": true,
  "pixel_embedded": true,
  "phash": "a3f5c8d2e1b4f7a9"
}

POST /api/v2/images/detect
{
  "image": "base64...",
  "mode": "deep"  // or "fast"
}

Response:
{
  "status": "VERIFIED" | "LIKELY_VERIFIED" | "SUSPICIOUS" | "NOT_DETECTED",
  "confidence": 0.95,
  "layers": {
    "latent_watermark": {"detected": true, "confidence": 0.92},
    "perceptual_hash": {"matched": true, "distance": 5},
    "cnn_classifier": {"ai_generated": true, "confidence": 0.88},
    "vit_detector": {"ai_generated": true, "confidence": 0.85}
  },
  "image_id": "img-uuid",
  "metadata": {...}
}

FILES TO UPDATE:
- api/v2/register.py
- api/v2/detect.py
- README.md (API documentation)

DEPLOYMENT:
- Docker build
- Run integration tests
- Deploy to staging
- Smoke test
- Deploy to production
```

---

## EMERGENCY CONTACT INFO

If you get stuck:
1. Check the PRD for specifications
2. Review the task breakdown for step-by-step guidance
3. Consult the ultimate_watermarking_solution.md for technical details
4. Ask Antigravity specific questions with context from these files

**Remember:** Build incrementally. Test frequently. Document everything.

**You've got this.** 🚀
