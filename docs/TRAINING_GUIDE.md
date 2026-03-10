# PROVENA Trinity — Model Training Guide

## Overview

This guide covers training the two ML model groups required for full Trinity capability:
1. **Pixel watermark encoder-decoder** (`WatermarkEncoder` + `WatermarkDecoder`)
2. **Detection classifiers** (`CNNClassifier` + `ViTDetector`)

> **Google Colab:** Recommended for GPU access. Runtime → Change runtime type → T4 GPU.

---

## Step 0 — Environment Setup

### In Colab:

```python
# Mount Drive (to save checkpoints)
from google.colab import drive
drive.mount('/content/drive')

# Clone repo
!git clone https://github.com/your-org/ai-image-provenance-system.git
%cd ai-image-provenance-system

# Install dependencies
!pip install -q -r requirements.txt
!pip install -q lpips timm einops accelerate
```

### Dataset Layout

```
datasets/
├── ai_generated/           # JPEG/PNG images from SD, DALL-E, Midjourney
│   ├── sdxl/           # ~50K images
│   ├── dalle3/         # ~50K images
│   └── midjourney/     # ~50K images
└── natural/            # Real photos
    ├── coco/           # COCO 2017 val+train
    └── lsun/           # LSUN bedroom, church, …
```

---

## Phase 1 — Pixel Watermark Training

**Script:** `src/training/train_watermark.py`

### Architecture Overview

```
Loss = λ_bit × BCE(predicted_bits, target_bits)
     + λ_lpips × LPIPS(watermarked, original)
     + λ_mse × MSE(watermarked, original)
     + λ_adv × adversarial_loss
```

Default weights: `λ_bit=10, λ_lpips=1, λ_mse=1, λ_adv=0.1`

### Training Command

```bash
python src/training/train_watermark.py \
    --data-dir datasets/ \
    --output-dir models/ \
    --epochs 100 \
    --batch-size 16 \
    --payload-bits 160 \
    --image-size 256 \
    --lr 1e-4 \
    --lambda-bit 10 \
    --lambda-lpips 1.0 \
    --lambda-mse 1.0 \
    --lambda-adv 0.1 \
    --device cuda \
    --save-every 10 \
    --log-wandb    # optional: wandb logging
```

### Alternative: Colab Training Cell

```python
import sys
sys.path.insert(0, "/content/ai-image-provenance-system")

from src.training.train_watermark import train_watermark
from src.training.config import WatermarkTrainingConfig

cfg = WatermarkTrainingConfig(
    data_dir       = "/content/datasets",
    output_dir     = "/content/drive/MyDrive/provena_models",
    epochs         = 100,
    batch_size     = 16,
    payload_bits   = 160,
    image_size     = 256,
    lr             = 1e-4,
    lambda_bit     = 10.0,
    lambda_lpips   = 1.0,
    device         = "cuda",
)

train_watermark(cfg)
```

### Expected Results After Training

| Metric | Target | Notes |
|--------|--------|-------|
| Bit accuracy (clean) | ≥ 99% | Round-trip on clean images |
| PSNR | ≥ 40 dB | Visual imperceptibility |
| SSIM | ≥ 0.98 | |
| Bit accuracy (JPEG Q=75) | ≥ 95% | |
| Bit accuracy (JPEG Q=50) | ≥ 85% | |
| Bit accuracy (resize 0.5×) | ≥ 80% | After resize back to original |

### Training Monitoring

Training logs checkpoints every 10 epochs to `models/checkpoints/`:

```
models/
├── checkpoints/
│   ├── encoder_epoch10.pth
│   ├── encoder_epoch20.pth
│   └── …
├── pixel_encoder.pth      ← best encoder
└── pixel_decoder.pth      ← best decoder
```

Metrics logged to `logs/watermark_training.csv`:

```
epoch, bit_accuracy, psnr, ssim, lpips, loss_bit, loss_lpips
```

---

## Phase 2 — Adversarial Hardening

After initial convergence (epoch 30+), enable the adversarial attacker:

```bash
python src/training/train_watermark.py \
    --resume models/checkpoints/encoder_epoch30.pth \
    --enable-attacker \
    --attacker-epochs 50 \
    --lambda-adv 0.5
```

The U-Net attacker applies random elastic+geometric transforms + style transfer
to try to erase the watermark. The encoder+decoder pair learns to be robust.

---

## Phase 3 — Detection Classifier Training

### Step 3.1: Generate Training Data

```python
# Generate 100K registered images for the "AI + watermarked" class
python scripts/generate_training_data.py \
    --n-images 100000 \
    --source-dir datasets/ai_generated/ \
    --encoder-path models/pixel_encoder.pth \
    --output-dir datasets/watermarked/ \
    --device cuda
```

### Step 3.2: Train CNN Classifier

```bash
python src/training/train_classifier.py \
    --model cnn \
    --watermarked-dir datasets/watermarked/ \
    --natural-dir datasets/natural/ \
    --output models/cnn_classifier.pth \
    --epochs 30 \
    --batch-size 64 \
    --lr 1e-4 \
    --scheduler cosine \
    --mixup 0.1 \
    --device cuda
```

### Step 3.3: Train ViT Detector

```bash
python src/training/train_classifier.py \
    --model vit \
    --watermarked-dir datasets/watermarked/ \
    --natural-dir datasets/natural/ \
    --output models/vit_detector.pth \
    --epochs 30 \
    --batch-size 32 \
    --lr 5e-5 \
    --scheduler cosine \
    --random-erasing 0.2 \
    --device cuda
```

### Expected Results After Training

| Metric | Target | Notes |
|--------|--------|-------|
| CNN accuracy | ≥ 98% | On balanced test set |
| ViT accuracy | ≥ 97% | |
| False positive rate | < 0.1% | Natural images misclassified |
| CNN throughput (GPU) | ≥ 100 img/s | |
| ViT throughput (GPU) | ≥ 50 img/s | |

---

## Step 4 — Deploy Trained Weights

```bash
# Copy to models/ directory
cp /content/drive/MyDrive/provena_models/pixel_encoder.pth models/
cp /content/drive/MyDrive/provena_models/pixel_decoder.pth models/
cp /content/drive/MyDrive/provena_models/cnn_classifier.pth models/
cp /content/drive/MyDrive/provena_models/vit_detector.pth models/

# Verify (health check)
curl http://localhost:5000/health | jq .trinity_mode
# Expected: "full"
```

---

## Troubleshooting Training

### Out of Memory (OOM)

```bash
# Reduce batch size
--batch-size 8

# Use gradient checkpointing
--gradient-checkpointing

# Use mixed precision
--amp fp16
```

### PSNR Below Target

- Increase `λ_lpips` relative to `λ_bit` (e.g., `--lambda-lpips 2.0`)
- Lower the `α` (embedding strength)
- Check that input images are normalised correctly

### Bit Accuracy Plateau

- Enable adversarial attacker earlier
- Increase learning rate warmup
- Check LDPC code is correctly applied to payloads

---

## Checkpoint Files

| File | Contents | Size (approx.) |
|------|---------|---------------|
| `models/pixel_encoder.pth` | Encoder state dict + config | ~200 MB |
| `models/pixel_decoder.pth` | Decoder state dict + config | ~80 MB |
| `models/cnn_classifier.pth` | EfficientNet-B4 fine-tuned | ~80 MB |
| `models/vit_detector.pth` | ViT-Small fine-tuned | ~85 MB |

**Total:** ~445 MB — recommended to store in Google Drive / cloud storage.
