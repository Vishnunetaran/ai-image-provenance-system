# PROVENA Trinity - Complete Task Breakdown
## From PRD to Implementation

---

## Phase 1: Latent Watermarking (Weeks 1-8)

### Week 1: Research & Setup

**Tasks:**

**1.1 Literature Review (3 days)**
- [ ] Read Tree-Ring paper (Wen et al., 2024) - 1 day
- [ ] Study diffusion model architecture (Stable Diffusion) - 1 day
- [ ] Review ChaCha20 stream cipher implementation - 0.5 day
- [ ] Document key learnings in `/docs/tree_ring_analysis.md` - 0.5 day

**1.2 Development Environment (2 days)**
- [ ] Set up GPU environment (4x A100 or equivalent) - 0.5 day
- [ ] Install dependencies: `torch`, `diffusers`, `transformers` - 0.5 day
- [ ] Fork Stable Diffusion codebase to `/models/stable_diffusion/` - 0.5 day
- [ ] Configure MLflow for experiment tracking - 0.5 day

**Deliverable:** Development environment ready, research documented

---

### Week 2: Core Implementation - Noise Injection

**Tasks:**

**2.1 Pattern Generation (3 days)**
- [ ] Implement ChaCha20 keyed pattern generator - 1 day
  - File: `src/watermarking/latent/pattern_generator.py`
  - Function: `generate_pattern(image_id, secret_key, shape)`
  
- [ ] Create payload encoder (UUID + timestamp → 160 bits) - 1 day
  - File: `src/watermarking/latent/payload.py`
  - Function: `encode_payload(uuid, timestamp)`
  
- [ ] Test pattern randomness and uniqueness - 1 day
  - Generate 10K patterns, verify no collisions
  - Statistical randomness tests

**2.2 Diffusion Modification (2 days)**
- [ ] Identify injection points in U-Net (T/2, T/4, T/8) - 1 day
  - File: `src/watermarking/latent/diffusion_injector.py`
  
- [ ] Implement noise pattern addition during reverse diffusion - 1 day
  - Modify `diffusers` pipeline
  - Add watermark tensor to noise at each timestep

**Deliverable:** Pattern generation + noise injection working

---

### Week 3: Watermark Extraction

**Tasks:**

**3.1 Extractor Implementation (3 days)**
- [ ] Implement correlation-based detector - 1.5 days
  - File: `src/watermarking/latent/extractor.py`
  - Correlate image with known pattern
  
- [ ] Add LDPC decoder for bit extraction - 1.5 days
  - Library: `pyldpc`
  - Function: `decode_watermark(correlation_map)`

**3.2 Testing & Calibration (2 days)**
- [ ] Test on 1000 clean images - 0.5 day
  - Measure extraction accuracy (target: 99%+)
  
- [ ] Calibrate detection threshold - 0.5 day
  - Balance false positives vs. false negatives
  
- [ ] Test against basic transforms (JPEG Q=75, 90) - 1 day
  - Measure degradation

**Deliverable:** End-to-end latent watermarking working on clean images

---

### Week 4: Robustness Testing

**Tasks:**

**4.1 Transform Testing (4 days)**
- [ ] Create test dataset: 5000 images with watermarks - 0.5 day
  
- [ ] Test JPEG compression (Q=10, 30, 50, 75, 90) - 1 day
  - Record extraction rates for each quality
  
- [ ] Test resizing (0.25x, 0.5x, 0.75x, 1.5x, 2x) - 1 day
  
- [ ] Test screenshot simulation - 0.5 day
  - Add blur + chroma subsampling
  
- [ ] Test cropping (10%, 20%, 30%) - 0.5 day
  
- [ ] Test color adjustments (brightness, contrast) - 0.5 day

**4.2 Documentation (1 day)**
- [ ] Create benchmark report - 0.5 day
  - Tables, graphs, failure analysis
  
- [ ] Document API usage - 0.5 day
  - `README_LATENT.md` with examples

**Deliverable:** Benchmark report showing ≥85% extraction at JPEG Q=50

---

### Week 5-6: Integration with Existing System

**Tasks:**

**5.1 API Integration (3 days)**
- [ ] Modify `/api/v1/images/register` endpoint - 1 day
  - Add latent watermark before pixel watermark
  
- [ ] Update verification endpoint - 1 day
  - Try latent extraction first
  
- [ ] Add configuration options - 0.5 day
  - Enable/disable latent watermarking
  - Adjust strength parameter
  
- [ ] Unit tests for API changes - 0.5 day

**5.2 Database Schema Updates (1 day)**
- [ ] Add `latent_watermark_present` column to provenance table - 0.5 day
- [ ] Migration script - 0.5 day

**5.3 End-to-End Testing (2 days)**
- [ ] Integration tests: Register → Verify workflow - 1 day
- [ ] Performance testing: Latency measurements - 0.5 day
- [ ] Load testing: 100 concurrent requests - 0.5 day

**Deliverable:** Latent watermarking integrated into PROVENA

---

### Week 7-8: Optimization & Buffer

**Tasks:**

**7.1 Performance Optimization (2 days)**
- [ ] Profile watermark embedding - 0.5 day
  - Identify bottlenecks
  
- [ ] Optimize pattern generation (caching) - 0.5 day
  
- [ ] Optimize extraction (GPU acceleration) - 1 day

**7.2 Edge Case Handling (2 days)**
- [ ] Handle detection failures gracefully - 0.5 day
- [ ] Add retry logic for low-confidence extractions - 0.5 day
- [ ] Improve error messages - 0.5 day
- [ ] Additional unit tests - 0.5 day

**7.3 Documentation & Demo (2 days)**
- [ ] Technical documentation (architecture, algorithms) - 1 day
- [ ] Create demo video (before/after comparison) - 0.5 day
- [ ] Prepare presentation for MNC - 0.5 day

**Deliverable:** Production-ready latent watermarking, Phase 1 complete

---

## Phase 2: Pixel Watermarking & Multi-Layer Detection (Weeks 9-16)

### Week 9-10: Data Collection & Preparation

**Tasks:**

**9.1 Dataset Curation (4 days)**
- [ ] Collect 50K AI-generated images (positive) - 1 day
  - From MNC's AI models
  - Variety of content types
  
- [ ] Collect 50K natural/other images (negative) - 1 day
  - Camera photos, stock images
  - Other AI models (if available)
  
- [ ] Label and organize dataset - 1 day
  - Train: 40K positive + 40K negative
  - Val: 5K positive + 5K negative
  - Test: 5K positive + 5K negative
  
- [ ] Create data loaders with augmentation - 1 day
  - PyTorch DataLoader
  - Augmentations: JPEG, resize, crop, blur, color jitter

**Deliverable:** Dataset ready for training

---

### Week 11-12: Encoder-Decoder Implementation

**Tasks:**

**11.1 Model Architecture (3 days)**
- [ ] Implement CNN+ViT hybrid encoder - 1.5 days
  - File: `src/watermarking/pixel/encoder.py`
  - Architecture: ResNet-50 (CNN) + ViT-Small (4 layers)
  
- [ ] Implement CNN+ViT hybrid decoder - 1.5 days
  - File: `src/watermarking/pixel/decoder.py`
  - Mirror encoder architecture

**11.2 Training Pipeline (2 days)**
- [ ] Implement training loop - 1 day
  - File: `src/watermarking/pixel/train.py`
  - Joint encoder-decoder training
  
- [ ] Add loss functions - 0.5 day
  - Watermark accuracy loss
  - Perceptual quality loss (LPIPS)
  - Combined weighted loss
  
- [ ] Configure optimizers and schedulers - 0.5 day
  - AdamW optimizer
  - Cosine annealing scheduler

**11.3 Initial Training (3 days)**
- [ ] Train on clean images (no augmentation) - 2 days
  - 50 epochs
  - Monitor PSNR, SSIM, extraction accuracy
  
- [ ] Validate on clean images - 0.5 day
  - Target: 99%+ extraction accuracy
  
- [ ] Debug and iterate - 0.5 day

**Deliverable:** Encoder-decoder working on clean images

---

### Week 13-14: Adversarial Training

**Tasks:**

**13.1 Removal Network (2 days)**
- [ ] Implement U-Net removal network - 1 day
  - File: `src/watermarking/pixel/attacker.py`
  - Goal: Remove watermark while preserving quality
  
- [ ] Test removal network effectiveness - 1 day
  - Baseline: How easily can it remove watermark?

**13.2 Adversarial Training Loop (4 days)**
- [ ] Implement min-max training - 2 days
  - Update encoder/decoder to resist removal
  - Update attacker to improve removal
  - Alternating optimization
  
- [ ] Add heavy augmentations - 1 day
  - JPEG Q=10-95
  - Resize 0.25x-2x
  - Gaussian blur, noise
  - Color jittering
  
- [ ] Train for 100 epochs - 1 day
  - Monitor: Watermark accuracy vs. removal success
  - Goal: High accuracy despite attacks

**Deliverable:** Adversarially robust encoder-decoder

---

### Week 15: CNN & ViT Detectors

**Tasks:**

**15.1 CNN Style Classifier (2 days)**
- [ ] Implement EfficientNet-B4 classifier - 0.5 day
  - File: `src/detection/cnn_classifier.py`
  - Binary classification: AI-generated vs. natural
  
- [ ] Train on dataset - 1 day
  - 30 epochs with augmentations
  - Target: 90%+ accuracy after transforms
  
- [ ] Validate and calibrate threshold - 0.5 day

**15.2 ViT Global Detector (3 days)**
- [ ] Implement ViT-Small detector - 0.5 day
  - File: `src/detection/vit_detector.py`
  - Focus on global composition patterns
  
- [ ] Train on dataset - 2 days
  - 50 epochs
  - Target: 85%+ accuracy
  
- [ ] Validate - 0.5 day

**Deliverable:** Two trained detection models

---

### Week 16: Ensemble Integration & Testing

**Tasks:**

**16.1 Ensemble Pipeline (2 days)**
- [ ] Implement 4-layer detector - 1 day
  - File: `src/detection/ensemble.py`
  - Combine: Watermark + pHash + CNN + ViT
  
- [ ] Implement voting logic - 0.5 day
  - Weighted voting based on confidence
  
- [ ] API integration - 0.5 day
  - Update `/api/v1/images/verify`

**16.2 Comprehensive Testing (3 days)**
- [ ] Test on 10K images with 200+ transforms - 1.5 days
  - Every combination of JPEG, resize, crop, etc.
  
- [ ] Measure metrics - 0.5 day
  - Detection rates, false positives, latency
  
- [ ] Generate benchmark report - 1 day
  - Tables, graphs, analysis

**Deliverable:** Complete Trinity system with 99% detection

---

## Quick Reference: File Structure

```
PROVENA/
├── src/
│   ├── watermarking/
│   │   ├── latent/
│   │   │   ├── pattern_generator.py
│   │   │   ├── payload.py
│   │   │   ├── diffusion_injector.py
│   │   │   └── extractor.py
│   │   └── pixel/
│   │       ├── encoder.py
│   │       ├── decoder.py
│   │       ├── attacker.py
│   │       └── train.py
│   └── detection/
│       ├── ensemble.py
│       ├── cnn_classifier.py
│       └── vit_detector.py
├── tests/
│   ├── test_latent.py
│   ├── test_pixel.py
│   └── test_ensemble.py
├── configs/
│   └── trinity_config.yaml
└── docs/
    ├── tree_ring_analysis.md
    ├── README_LATENT.md
    └── benchmarks/
```

---

## Checkpoint Gates (Go/No-Go Decisions)

### Checkpoint 1 (End of Week 4)
**Criteria:**
- ✅ Latent watermark extraction ≥85% on clean images
- ✅ ≥70% extraction after JPEG Q=50
- ✅ Code reviewed and tested

**Go:** Continue to integration  
**No-Go:** Debug and iterate for 1 week, then re-evaluate

---

### Checkpoint 2 (End of Week 8)
**Criteria:**
- ✅ Latent watermarking integrated into PROVENA
- ✅ All tests passing
- ✅ MNC demo successful

**Go:** Proceed to Phase 2  
**No-Go:** Re-scope Phase 2 or pivot

---

### Checkpoint 3 (End of Week 14)
**Criteria:**
- ✅ Pixel watermark ≥95% extraction at JPEG Q=50
- ✅ Adversarial training showing improvement
- ✅ Models converging

**Go:** Continue to detection layers  
**No-Go:** Increase training time or adjust architecture

---

### Final Checkpoint (End of Week 16)
**Criteria:**
- ✅ Combined detection ≥99% at JPEG Q=50
- ✅ False positive rate <0.1%
- ✅ Latency <300ms

**Go:** Deploy to production  
**No-Go:** Additional optimization or accept reduced targets

---

## Daily Standup Template

**Yesterday:**
- Tasks completed
- Blockers encountered

**Today:**
- Tasks planned
- Expected completion

**Risks:**
- Technical challenges
- Resource needs

---

## Tools & Technologies Checklist

**Programming:**
- [ ] Python 3.10+
- [ ] PyTorch 2.0+
- [ ] Node.js (for Antigravity integration)

**ML Libraries:**
- [ ] `diffusers` (Hugging Face)
- [ ] `transformers`
- [ ] `timm` (PyTorch Image Models)
- [ ] `pyldpc` (error correction)

**Utilities:**
- [ ] MLflow (experiment tracking)
- [ ] Weights & Biases (optional alternative)
- [ ] pytest (testing)
- [ ] Black (code formatting)

**Infrastructure:**
- [ ] GPU access (4-8x A100)
- [ ] S3 or equivalent (dataset storage)
- [ ] PostgreSQL (registry database)
- [ ] Redis (caching)

---

## Budget Tracking Template

| Category | Budgeted | Actual | Remaining |
|----------|----------|--------|-----------|
| Personnel (2 engineers, 4 months) | $120,000 | $ | $ |
| GPU compute (A100, 4 months) | $50,000 | $ | $ |
| Cloud infrastructure | $15,000 | $ | $ |
| Tools & licenses | $5,000 | $ | $ |
| Contingency | $10,000 | $ | $ |
| **Total** | **$200,000** | **$** | **$** |

---

## Success Metrics Dashboard

Track these weekly:

| Metric | Week 4 | Week 8 | Week 12 | Week 16 | Target |
|--------|--------|--------|---------|---------|--------|
| Latent Detection (Clean) | | | | | 99% |
| Latent Detection (JPEG Q=50) | | | | | 85% |
| Pixel Detection (JPEG Q=50) | | | | | 95% |
| Combined Detection (JPEG Q=50) | | | | | 99% |
| False Positive Rate | | | | | <0.1% |
| Latency (ms) | | | | | <300 |

---

**This task list is your roadmap. Follow it, track progress daily, adjust as needed. You've got this.** 🚀
