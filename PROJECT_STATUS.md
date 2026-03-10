# PROVENA Trinity v2 — Project Status

> **Branch:** `feature/trinity-v2-api`
> **Last updated:** 2026-03-10
> **Overall status:** Flask API layer complete ✅ · ML model implementations pending ⏳

---

## Quick Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Fully implemented and tested |
| 🔧 | Implemented as stub / scaffold (compiles, runs, but untrained) |
| ⏳ | Not yet started — needs to be built |

---

## 1. Flask Application Layer — COMPLETE ✅

### Core App

| File | Status | Notes |
|------|--------|-------|
| `provena_flask/__init__.py` | ✅ | `create_app()` factory, `TrinityServices` container, graceful degradation, blueprint registration, health endpoint |
| `provena_flask/config.py` | ✅ | All Trinity v2 settings: model paths, thresholds, secret key, device, ENSEMBLE_WEIGHTS |
| `app.py` | ✅ | Entry point; loads `.env`, prints startup summary with Trinity mode |

### v2 API Blueprints (`provena_flask/api_v2/`)

| File | Endpoint | Status |
|------|----------|--------|
| `register.py` | `POST /api/v2/images/register` | ✅ |
| `detect.py` | `POST /api/v2/images/detect` | ✅ |
| `watermark_v2.py` | `GET/POST /api/v2/watermark/*` | ✅ |

### Database & Services

| File | Status | Notes |
|------|--------|-------|
| `provena_flask/models/provenance.py` | ✅ | Schema v2 with Trinity columns; `migrate_schema()` for existing DBs |
| `provena_flask/services/registry_service.py` | ✅ | Updated with Trinity flags, relaxed validation for unsigned records |

---

## 2. ML Source Modules — PENDING ⏳

> **Critical missing pieces.** Flask API calls into these — they must be built.

### 2a. Latent Watermarking — `src/watermarking/latent/` — ⏳ NOT STARTED

| File | What to implement |
|------|-------------------|
| `extractor.py` | `LatentWatermarkExtractor.extract(img_rgb)` → `(result, confidence)` |
| `pattern_generator.py` | `generate_pattern(image_id, secret_key, shape)` via ChaCha20 CSPRNG |
| `payload.py` | `encode_payload(image_id, ts)` → 160 bits; `decode_payload(bits)` with ECC |
| `diffusion_injector.py` | Spatial/DCT injection of pattern at strength α |

> **Building this = Trinity mode changes `"unavailable"` → `"latent_only"`. All v2 endpoints work.**

### 2b. Pixel Watermarking — `src/watermarking/pixel/`

| File | Status | Notes |
|------|--------|-------|
| `encoder.py` | 🔧 Scaffold | Class + forward() exists; weights random (needs Colab training) |
| `decoder.py` | 🔧 Scaffold | Class + forward() exists; weights random (needs Colab training) |
| `attacker.py` | ⏳ | U-Net adversarial attacker for training robustness |
| `losses.py` | ⏳ | `λ_bit × BCE + λ_lpips × LPIPS + λ_mse × MSE` |

### 2c. Detection — `src/detection/` — ⏳ NOT STARTED

| File | What to implement |
|------|-------------------|
| `cnn_classifier.py` | `CNNClassifier.predict(img_rgb)` → `(detected, confidence)` — EfficientNet-B4 |
| `vit_detector.py` | `ViTDetector.predict(img_rgb)` → `(detected, confidence)` — ViT-Small |
| `ensemble.py` | `TrinityDetector` facade + `DetectionError` exception |

---

## 3. Training Infrastructure — `src/training/` — ⏳ NOT STARTED

| File | Purpose |
|------|---------|
| `train_watermark.py` | Encoder-decoder training loop (PSNR/SSIM/bit_accuracy targets) |
| `train_classifier.py` | CNN + ViT classifier training |
| `config.py` | `WatermarkTrainingConfig`, `ClassifierTrainingConfig` |
| `dataset.py` | Dataset classes for watermarked vs natural images |

---

## 4. Tests — COMPLETE ✅

| File | Runs Now? | Notes |
|------|:---------:|-------|
| `tests/test_ensemble.py` | ✅ Yes | Pure logic — no models needed |
| `tests/test_model_loading.py` | ✅ Yes | Tests graceful fallback |
| `tests/test_pixel.py` | ✅ Structural | Accuracy tests need `TRAINED_MODELS=1` |
| `tests/test_integration_v2.py` | ⏳ After latent src | Needs `src.watermarking.latent` importable |

---

## 5. Benchmarks, Docs, Scripts — COMPLETE ✅

| Area | Files Done |
|------|-----------|
| Benchmarks | `latent_benchmark.py`, `ensemble_benchmark.py`, `generate_report.py` |
| Docs | `docs/API_V2.md`, `docs/TRINITY_ARCHITECTURE.md`, `docs/TRAINING_GUIDE.md` |
| Scripts | `scripts/verify_install.py`, `scripts/download_models.py`, `scripts/run_benchmarks.py` |
| Config | `.env.example` (extended), `requirements.txt` (updated) |

---

## What To Build Next — Priority Order

### 🔴 Priority 1  — `src/watermarking/latent/` (4 files, ~400 lines, pure Python)

Unlocks: latent mode, all integration tests, register/detect fully working

```
payload.py           → encode/decode UUID+timestamp with ECC
pattern_generator.py → ChaCha20-keyed noise field
diffusion_injector.py→ inject pattern into image pixels
extractor.py         → correlation detection + blind search
```

### 🟡 Priority 2 — `src/detection/` (3 files, model wrappers)

Unlocks: CNN/ViT layers, full mode when `.pth` files exist

```
cnn_classifier.py  → EfficientNet-B4 wrapper
vit_detector.py    → ViT-Small wrapper
ensemble.py        → TrinityDetector facade
```

### 🟡 Priority 3 — `src/training/` (Colab GPU required)

Unlocks: real watermarking accuracy, trained `.pth` files

### 🟢 Priority 4 — Polish

- `README.md` update for v2 API
- `setup.py` creation
- `.gitignore` additions (`models/*.pth`, `data/`, `results/`)

---

## Current Trinity Mode

```
"unavailable"  ← right now (src/watermarking/latent not built)
     ↓   Build Priority 1
"latent_only"  ← register + detect with layers 1+2
     ↓   Build Priority 2 + train models
"full"         ← all 4 layers active
```

---

## How to Run Right Now

```bash
# Verify installation
python scripts/verify_install.py

# Start server (mode = unavailable until latent src built)
python app.py

# Health check
curl http://localhost:5000/health

# Tests that pass today (no models, no GPU)
pytest tests/test_ensemble.py tests/test_model_loading.py -v
```

---

## File Count

| Area | Done | Remaining |
|------|:----:|:---------:|
| Flask API v2 | 4 | 0 |
| Flask core | 3 | 0 |
| DB / Services | 2 | 0 |
| `src/watermarking/latent/` | 0 | **4** ← start here |
| `src/watermarking/pixel/` | 2 (scaffold) | 2 |
| `src/detection/` | 0 | **3** |
| `src/training/` | 0 | 5 |
| Tests | 4 new + 3 existing | 0 |
| Benchmarks | 3 | 0 |
| Docs | 3 new + existing | README update |
| Scripts | 3 | 0 |
| Config | 2 | setup.py |
| **Total** | **~26** | **~14** |
