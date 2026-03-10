# PROVENA Trinity v2 — API Reference

## Base URL

```
http://localhost:5000/api/v2
```

---

## Endpoints

### `GET /api/v2/watermark/info`

Returns current system capabilities and loaded models.

**Response 200:**
```json
{
  "mode": "latent_only",
  "available_layers": {
    "latent_watermark": true,
    "perceptual_hash": true,
    "cnn_classifier": false,
    "vit_detector": false
  },
  "payload_bits": 160,
  "device": "cpu",
  "pixel_image_size": 256,
  "thresholds": {
    "watermark_confidence": 0.9,
    "phash_match": 15,
    "cnn_confidence": 0.8,
    "vit_confidence": 0.8,
    "ensemble": 0.75
  }
}
```

**Curl:**
```bash
curl http://localhost:5000/api/v2/watermark/info
```

---

### `POST /api/v2/images/register`

Register an AI-generated image with PROVENA Trinity watermarks.

**Request:**
```json
{
  "image":         "<base64-encoded image>",
  "model_id":      "stable-diffusion-xl-v1",
  "prompt_hash":   "sha256:abc...",
  "metadata":      {},
  "enable_latent": true,
  "enable_pixel":  true
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `image` | string (base64) | ✅ | — |
| `model_id` | string | ❌ | `"unknown"` |
| `prompt_hash` | string | ❌ | `null` |
| `metadata` | object | ❌ | `{}` |
| `enable_latent` | bool | ❌ | `true` |
| `enable_pixel` | bool | ❌ | `true` |

**Response 201:**
```json
{
  "image_id":              "img-550e8400-e29b-41d4-a716-446655440000",
  "watermarked_image":     "<base64-PNG>",
  "latent_embedded":       true,
  "pixel_embedded":        false,
  "perceptual_hashes": {
    "phash": "f0f8e0c0a0908080",
    "dhash": "7e3c1c0e0e0e0e1e",
    "ahash": "fffef0f0f8fcfcfc"
  },
  "signature":             "3d4f...",
  "key_id":                "key-abc123",
  "timestamp":             "2026-02-24T07:12:42+00:00",
  "model_id":              "stable-diffusion-xl-v1",
  "provenance_registered": true,
  "processing_ms":         45.2
}
```

**Status Codes:**
| Code | Meaning |
|------|---------|
| 201 | Registered successfully |
| 400 | Missing/invalid `image` field |
| 415 | Content-Type must be `application/json` |
| 500 | Internal server error |

**Curl:**
```bash
# Encode image
B64=$(base64 -w0 my_image.png)

curl -s -X POST http://localhost:5000/api/v2/images/register \
  -H "Content-Type: application/json" \
  -d "{\"image\": \"$B64\", \"model_id\": \"sdxl-v1\"}" | jq .
```

**Python:**
```python
import requests, base64

with open("image.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

resp = requests.post(
    "http://localhost:5000/api/v2/images/register",
    json={"image": b64, "model_id": "sdxl-v1"}
)
print(resp.json())
```

---

### `POST /api/v2/images/detect`

Detect whether an image was generated and registered by PROVENA Trinity.

**Request:**
```json
{
  "image": "<base64-encoded image>",
  "mode":  "standard"
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `image` | string (base64) | ✅ | — |
| `mode` | `"fast"` \| `"standard"` \| `"deep"` | ❌ | `"standard"` |

**Detection Mode Comparison:**

| Mode | Layers Active | Typical Latency | Best For |
|------|:-------------:|:---------------:|----------|
| `fast` | 1, 2 | 1–50 ms | High-throughput, approximate |
| `standard` | 1, 2, 3 | 50–200 ms | Balanced (recommended) |
| `deep` | 1, 2, 3, 4 | 200–500 ms | Maximum accuracy |

**Response 200:**
```json
{
  "status":          "VERIFIED",
  "confidence":      0.9732,
  "image_id":        "img-550e8400-...",
  "detected_layers": ["latent_watermark", "perceptual_hash"],
  "mode":            "standard",
  "layers": [
    {
      "layer":      "latent_watermark",
      "ran":        true,
      "detected":   true,
      "confidence": 0.953,
      "image_id":   "img-550e8400-...",
      "details":    { "latency_ms": 12.4 }
    },
    {
      "layer":      "perceptual_hash",
      "ran":        true,
      "detected":   true,
      "confidence": 0.902,
      "image_id":   "img-550e8400-...",
      "details":    { "query_phash": "f0f8...", "best_distance": 4, "candidates": 1 }
    },
    {
      "layer":      "cnn_classifier",
      "ran":        false,
      "detected":   false,
      "confidence": 0.0,
      "details":    { "reason": "model not loaded" }
    },
    {
      "layer":      "vit_detector",
      "ran":        false,
      "confidence": 0.0,
      "details":    { "reason": "mode=standard skips ViT" }
    }
  ],
  "processing_ms": 34.1
}
```

**Status Values:**

| Status | Confidence | Meaning |
|--------|:----------:|---------|
| `VERIFIED` | ≥ 0.95 | High confidence — watermark found in registry |
| `LIKELY_VERIFIED` | 0.75–0.95 | Probable match — some layers agree |
| `SUSPICIOUS` | 0.50–0.75 | AI-generated but may not be in registry |
| `NOT_DETECTED` | < 0.50 | No evidence of PROVENA watermark |

**Curl:**
```bash
B64=$(base64 -w0 suspect_image.png)

curl -s -X POST http://localhost:5000/api/v2/images/detect \
  -H "Content-Type: application/json" \
  -d "{\"image\": \"$B64\", \"mode\": \"deep\"}" | jq .status
```

---

### `POST /api/v2/watermark/embed`

Standalone watermark embedding (no provenance registration).

**Request:**
```json
{
  "image":    "<base64>",
  "image_id": "img-optional-custom-id",
  "type":     "latent"
}
```

| `type` | Description |
|--------|-------------|
| `latent` | Frequency-domain embedding only (default, always works) |
| `pixel` | Learned pixel-space embedding (requires trained encoder) |
| `both` | Both methods applied sequentially |

**Response 200:**
```json
{
  "image_id":          "img-...",
  "watermarked_image": "<base64-PNG>",
  "latent_embedded":   true,
  "pixel_embedded":    false,
  "processing_ms":     12.4
}
```

---

### `POST /api/v2/watermark/extract`

Standalone watermark extraction (no registry lookup).

**Request:**
```json
{
  "image": "<base64>",
  "type":  "latent"
}
```

**Response 200:**
```json
{
  "latent": {
    "detected":   true,
    "confidence": 0.921,
    "image_id":   "img-...",
    "timestamp":  1700000000
  },
  "processing_ms": 18.3
}
```

---

## Error Responses

All errors follow this schema:
```json
{
  "error":   "Bad request",
  "message": "Missing required field: image"
}
```

| HTTP Code | Reason |
|-----------|--------|
| 400 | Invalid request (missing field, bad base64, bad mode) |
| 415 | Content-Type must be `application/json` |
| 413 | Image too large (max 32 MB) |
| 500 | Server error (check logs) |

---

## v1 → v2 Migration Guide

| Feature | v1 (`/api/v1`) | v2 (`/api/v2`) |
|---------|:--------------:|:--------------:|
| Latent watermark | ❌ | ✅ |
| Pixel watermark | ❌ | ✅ (when trained) |
| Ensemble detection | ❌ | ✅ (4 layers) |
| Detection modes | 1 | 3 (fast/standard/deep) |
| Confidence score | ❌ | ✅ |
| Per-layer results | ❌ | ✅ |
| Graceful degradation | ❌ | ✅ |
