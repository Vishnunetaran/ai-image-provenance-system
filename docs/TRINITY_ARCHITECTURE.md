# PROVENA Trinity — Technical Architecture

## System Overview

PROVENA Trinity is a **hybrid AI-image watermarking and detection system** combining:

1. **Latent watermarking** — algorithmic (no training), always available
2. **Pixel watermarking** — learned CNN+ViT encoder-decoder, requires training
3. **4-layer ensemble detection** — latent + pHash + CNN + ViT

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROVENA Trinity System                        │
│                                                                  │
│  ┌─────────────────────┐    ┌──────────────────────────────┐   │
│  │   REGISTRATION PATH  │    │       DETECTION PATH          │  │
│  │                      │    │                               │  │
│  │  Image → Latent WM   │    │   Query Image                 │  │
│  │        ↓             │    │         │                     │  │
│  │  + Pixel WM (opt.)   │    │    ┌────▼───────────────┐    │  │
│  │        ↓             │    │    │  Layer 1: Latent    │    │  │
│  │  pHash computation   │    │    │  (correlation)      │ 40%│  │
│  │        ↓             │    │    └────────────────────┘    │  │
│  │  Ed25519 signature   │    │    ┌────────────────────┐    │  │
│  │        ↓             │    │    │  Layer 2: pHash     │    │  │
│  │  SQLite database  ◄──┼────┼────│  (registry lookup)  │ 30%│  │
│  │                      │    │    └────────────────────┘    │  │
│  │  Watermarked image   │    │    ┌────────────────────┐    │  │
│  │        ↓             │    │    │  Layer 3: CNN       │    │  │
│  │  Return to caller    │    │    │  (EfficientNet-B4)  │ 20%│  │
│  └─────────────────────┘    │    └────────────────────┘    │  │
│                              │    ┌────────────────────┐    │  │
│                              │    │  Layer 4: ViT       │    │  │
│                              │    │  (ViT-Small)        │ 10%│  │
│                              │    └────────────────────┘    │  │
│                              │         │                     │  │
│                              │    Weighted Ensemble Vote     │  │
│                              │         │                     │  │
│                              │    VERIFIED / LIKELY /        │  │
│                              │    SUSPICIOUS / NOT_DETECTED  │  │
│                              └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Latent Watermark (Tree-Ring Style)

**File:** `src/watermarking/latent/`

### Embedding Process

1. **Pattern generation** (`pattern_generator.py`):
   - ChaCha20 CSPRNG keyed with `TRINITY_SECRET_KEY + image_id`
   - Generates a (H, W) float32 noise pattern
   - Optional frequency-domain shaping (ring-shaped energy concentration)

2. **Payload encoding** (`payload.py`):
   - 128-bit UUID + 32-bit timestamp = 160-bit raw payload
   - 3-tier error correction fallback: LDPC → Reed-Solomon → Redundancy
   - CRC-16 integrity check prepended

3. **Injection** (`diffusion_injector.py`):
   - Adds `α × pattern` to image pixels (α ≈ 8.0)
   - Adaptive α based on local variance (preserves edges)
   - Multiple injection timesteps for robustness

### Extraction Process

1. Compute correlation between candidate pattern and image FFT
2. Blind extraction: try all candidate IDs from registry
3. Threshold: correlation ≥ 0.5 → watermark detected
4. ECC decode with 3-tier fallback to recover UUID + timestamp

**Robustness targets:**
- JPEG Q=50: ≥ 95% detection
- Resize 0.5×: ≥ 90% detection
- Crop 20%: ≥ 85% detection

---

## Layer 2 — Perceptual Hash Registry Lookup

**File:** `provena_flask/services/phash_service.py`

- Computes pHash, dHash, aHash for the query image
- Searches SQLite registry for stored hashes
- Hamming distance ≤ 15 → match detected
- Confidence = `1 - distance / (threshold + 1)`

**Properties:**
- No ML model required (always available)
- Fast: < 5 ms
- Tolerant: survives minor transformations
- Limited: fails on aggressive JPEG (Q < 30) or large crops

---

## Layer 3 — CNN Style Classifier

**File:** `src/detection/cnn_classifier.py`

- **Backbone:** EfficientNet-B4 (pretrained on ImageNet)
- **Head:** 2-class softmax (AI-generated vs natural)
- **Input:** 224×224 RGB, normalised to ImageNet stats
- **Output:** probability of being AI-generated

Fine-tuned on:
- 50K AI-generated images (SD, DALL-E, Midjourney, etc.)
- 50K natural photographs (COCO, LSUN)

Training: 30 epochs, AdamW, cosine LR schedule, mixup augmentation.

---

## Layer 4 — ViT Global Detector

**File:** `src/detection/vit_detector.py`

- **Backbone:** ViT-Small/16 (pretrained on ImageNet-21K)
- **Head:** 2-class MLP with dropout
- **Input:** 224×224 RGB
- **Strength:** Captures global patterns CNN misses

Fine-tuned with stronger augmentations (RandomErasing, ColorJitter ±30%).

---

## Layer 2 (Alternate) — Pixel Watermark Encoder-Decoder

**Files:** `src/watermarking/pixel/encoder.py`, `decoder.py`

### Encoder Architecture (CNN + ViT Hybrid)

```
Image (3, H, W)           Watermark (160,)
      │                         │
   EfficientNet-B4          Linear → 5×5 feature maps
   (feature extractor)           │
      │                    Transformer Encoder
   Skip connections              │
      │               ←──── Cross-attention fusion
      │
   Decoder (transposed convs + skip)
      │
   Residual Δ (3, H, W)   ← tanh-clamped to [-0.1, 0.1]
      │
   Watermarked Image = Image + Δ
```

**Key constraints:**
- PSNR ≥ 40 dB (visual imperceptibility)
- SSIM ≥ 0.98

### Decoder Architecture

```
Watermarked Image (3, H, W)
      │
   EfficientNet-B4 (shared or separate backbone)
      │
   Transformer Encoder (4 heads, 4 layers)
      │
   Linear head → 160 logits
      │
   Sigmoid → bit probabilities
```

### Adversarial Training

The U-Net attacker (`src/watermarking/pixel/attacker.py`) tries to erase the watermark.
Training follows a min-max game:
- Generator (Encoder) loss: `L_acc + λ_q × L_LPIPS`
- Discriminator (Attacker) loss: cross-entropy on erased bits

---

## Ensemble Voting Algorithm

Given layer results `{name: (ran, detected, confidence)}`:

```python
weighted_score = Σ weight[name] × confidence[name]  (for detected layers only)
total_weight   = Σ weight[name]  (for ran layers only)
final_score    = weighted_score / total_weight

status = (
    "VERIFIED"       if final_score >= 0.95 else
    "LIKELY_VERIFIED" if final_score >= 0.75 else
    "SUSPICIOUS"     if final_score >= 0.50 else
    "NOT_DETECTED"
)
```

**Default weights:** latent=0.40, pHash=0.30, CNN=0.20, ViT=0.10

Weights normalise over *ran* layers, so missing models don't deflate the score.

---

## Database Schema

**File:** `provena_flask/models/provenance.py`

```sql
CREATE TABLE provenance_records (
    image_id                TEXT PRIMARY KEY,
    model_id                TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    prompt_hash             TEXT,
    watermark_payload       BLOB NOT NULL,
    perceptual_hash         TEXT NOT NULL,
    signature               BLOB NOT NULL,
    public_key              BLOB NOT NULL,
    key_id                  TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    -- Trinity v2 columns (added in schema v2)
    latent_watermark_present INTEGER DEFAULT 0,
    pixel_watermark_present  INTEGER DEFAULT 0,
    detection_confidence     REAL,
    detection_layers         TEXT,    -- JSON blob
    last_detected_at         TEXT
);
```

**Design:** Append-only (no UPDATE/DELETE). All writes logged to `audit_log`.

---

## Configuration Reference

Key configuration values (see `provena_flask/config.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `TRINITY_SECRET_KEY` | 32-byte key | ChaCha20 key (set via env var) |
| `DEVICE` | `"auto"` | `"cuda"`, `"cpu"`, or `"auto"` |
| `WATERMARK_CONFIDENCE_THRESHOLD` | `0.9` | Latent layer detection threshold |
| `PHASH_MATCH_THRESHOLD` | `15` | Max Hamming distance for pHash match |
| `CNN_CONFIDENCE_THRESHOLD` | `0.8` | CNN layer detection threshold |
| `VIT_CONFIDENCE_THRESHOLD` | `0.8` | ViT layer detection threshold |
| `ENSEMBLE_THRESHOLD` | `0.75` | Min score for LIKELY_VERIFIED |
| `ALLOW_LATENT_ONLY_MODE` | `True` | Start without trained weights |

---

## Data Flow Diagrams

### Registration Flow

```
CLIENT                    API /register            TRINITY SERVICES        DATABASE
  │                           │                         │                      │
  │──POST /register──────────►│                         │                      │
  │   {image, model_id}       │──decode_base64──────────►                     │
  │                           │◄─img_rgb─────────────────                     │
  │                           │──generate_image_id───────►                    │
  │                           │──embed_latent_wm──────────►                   │
  │                           │◄─watermarked_img──────────                    │
  │                           │──embed_pixel_wm───────────►                   │
  │                           │  (if encoder loaded)        │                 │
  │                           │◄─watermarked_img2─────────                    │
  │                           │──compute_phash────────────►                   │
  │                           │──sign_record──────────────►                   │
  │                           │──registry.register──────────────────────────► │
  │◄─201 {image_id, wm_img}───│                         │                      │
```

### Detection Flow

```
CLIENT                    API /detect             ENSEMBLE                DATABASE
  │                           │                      │                       │
  │──POST /detect─────────────►                      │                       │
  │   {image, mode}           │──decode_image────────►                      │
  │                           │──run_latent_layer────►                      │
  │                           │──run_phash_layer──────►──────────────────── │
  │                           │                       │◄──DB.search_phash── │
  │                           │──run_cnn_layer────────►  (if mode≠fast)     │
  │                           │──run_vit_layer────────►  (if mode=deep)     │
  │                           │──ensemble_verdict──────►                    │
  │◄─200 {status, confidence}─│                       │                      │
```
