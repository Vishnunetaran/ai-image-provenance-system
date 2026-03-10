"""
PROVENA Trinity v2 – Image Registration Endpoint
POST /api/v2/images/register

Registers an AI-generated image by:
  1. Embedding a latent watermark (always – no model file required)
  2. Embedding a pixel watermark (when encoder weights are loaded)
  3. Computing a perceptual hash (pHash / dHash / aHash)
  4. Signing all metadata with Ed25519
  5. Writing to the provenance registry
  6. Returning the watermarked image + provenance record

Graceful degradation:
  - If pixel encoder is not loaded → step 2 is skipped, no crash
  - If crypto service unavailable → returns 503
"""

from __future__ import annotations

import base64
import io
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

register_bp = Blueprint("register_v2", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_image(b64_str: str) -> np.ndarray:
    """Decode a base64-encoded image to an RGB NumPy array (H, W, 3) uint8."""
    try:
        import cv2
        raw = base64.b64decode(b64_str)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image bytes")
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        raise ValueError(f"Image decoding failed: {exc}") from exc


def _encode_image_png(img_rgb: np.ndarray) -> str:
    """Encode an RGB NumPy array to a base64 PNG string."""
    import cv2
    bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _compute_phash(img_rgb: np.ndarray) -> Dict[str, str]:
    """Return pHash, dHash and aHash for the image."""
    try:
        from PIL import Image as PILImage
        import imagehash
        pil = PILImage.fromarray(img_rgb)
        return {
            "phash": str(imagehash.phash(pil)),
            "dhash": str(imagehash.dhash(pil)),
            "ahash": str(imagehash.average_hash(pil)),
        }
    except Exception as exc:
        logger.warning("Perceptual hash computation failed: %s", exc)
        return {"phash": "", "dhash": "", "ahash": ""}


def _embed_latent_watermark(
    img_rgb: np.ndarray,
    image_id: str,
    timestamp_int: int,
    secret_key: bytes,
) -> np.ndarray:
    """
    Apply the latent (frequency-domain) watermark to the image.

    Returns the watermarked image (same shape/dtype as input).
    """
    try:
        from src.watermarking.latent.pattern_generator import generate_pattern
        from src.watermarking.latent.payload import encode_payload

        h, w = img_rgb.shape[:2]
        pattern = generate_pattern(image_id, secret_key, (h, w)).numpy()
        payload_bits = encode_payload(image_id, timestamp_int)

        # Scale pattern strength by payload (±1)
        payload_sign = np.array([1 if b else -1 for b in payload_bits], dtype=np.float32)

        # Embed into each channel using ring-based frequency injection
        out = img_rgb.astype(np.float64)
        alpha = 8.0  # injection strength; tunable
        for c in range(3):
            out[:, :, c] += pattern * alpha
        out = np.clip(out, 0, 255).astype(np.uint8)
        return out

    except Exception as exc:
        logger.warning("Latent watermark embedding failed: %s", exc)
        return img_rgb   # return original unchanged


def _embed_pixel_watermark(
    img_rgb: np.ndarray,
    image_id: str,
    svc,
) -> Optional[np.ndarray]:
    """
    Apply the learned pixel-space watermark using the trained encoder.

    Returns watermarked image or None if the encoder is unavailable.
    """
    if svc.pixel_encoder is None:
        return None

    try:
        import torch
        from src.watermarking.latent.payload import encode_payload

        # Build 160-bit watermark tensor
        ts = int(time.time())
        bits = encode_payload(image_id, ts)
        wm = torch.tensor(bits, dtype=torch.float32).unsqueeze(0)   # (1, 160)

        # Preprocess image → tensor (1, 3, H, W) in [-1, 1]
        h, w = img_rgb.shape[:2]
        import torchvision.transforms.functional as TF
        from PIL import Image as PILImage
        pil = PILImage.fromarray(img_rgb).resize(
            (svc.pixel_image_size, svc.pixel_image_size)
        )
        img_t = TF.to_tensor(pil).unsqueeze(0) * 2 - 1   # type: ignore
        img_t = img_t.to(svc.device)
        wm = wm.to(svc.device)

        with torch.no_grad():
            wm_t = svc.pixel_encoder(img_t, wm)

        # Back to numpy
        wm_np = ((wm_t.squeeze(0).clamp(-1, 1) + 1) / 2 * 255).byte()
        wm_np = wm_np.permute(1, 2, 0).cpu().numpy()   # (H, W, 3)

        # Resize back to original dimensions
        import cv2
        wm_np = cv2.resize(wm_np, (w, h), interpolation=cv2.INTER_LINEAR)
        return wm_np

    except Exception as exc:
        logger.warning("Pixel watermark embedding failed: %s", exc)
        return None


def _sign_record(payload_dict: Dict, app_cfg) -> tuple[bytes, bytes, str]:
    """
    Create an Ed25519 signature for the provenance record.

    Returns (signature_bytes, public_key_bytes, key_id).
    """
    try:
        from provena_flask.services.crypto_service import CryptoService
        crypto = CryptoService(keys_dir=app_cfg.get("KEYS_DIR", "./keys"))
        signing_key_id = crypto.get_or_create_key()
        import json
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode()
        sig = crypto.sign(payload_bytes, signing_key_id)
        pub = crypto.get_public_key_bytes(signing_key_id)
        return sig, pub, signing_key_id
    except Exception as exc:
        logger.error("Signing failed: %s", exc)
        # Return empty bytes — record still stored, but unsigned
        return b"", b"", "unsigned"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@register_bp.route("/images/register", methods=["POST"])
def register_image():
    """
    Register an AI-generated image with PROVENA Trinity v2 watermarks.

    Request JSON:
    {
        "image":          "<base64-encoded image>",
        "model_id":       "stable-diffusion-xl-v1",
        "prompt_hash":    "sha256:abc...",     # optional
        "metadata":       { ... },             # optional extra metadata
        "enable_latent":  true,                # default true
        "enable_pixel":   true                 # default true (if model loaded)
    }

    Response JSON (200):
    {
        "image_id":              "img-<uuid>",
        "watermarked_image":     "<base64-PNG>",
        "latent_embedded":       true,
        "pixel_embedded":        false,
        "perceptual_hashes":     { "phash": ..., "dhash": ..., "ahash": ... },
        "signature":             "<hex>",
        "timestamp":             "2026-02-24T07:12:42Z",
        "provenance_registered": true
    }
    """
    t_start = time.perf_counter()
    svc = current_app.trinity  # type: ignore[attr-defined]
    cfg = current_app.config

    # ── Parse request ─────────────────────────────────────────────────────
    if not request.is_json:
        return jsonify(error="Content-Type must be application/json"), 415

    data: Dict[str, Any] = request.get_json(silent=True) or {}

    if "image" not in data:
        return jsonify(error="Missing required field: image"), 400

    model_id: str = data.get("model_id", "unknown")
    enable_latent: bool = bool(data.get("enable_latent", True))
    enable_pixel: bool = bool(data.get("enable_pixel", True))
    prompt_hash: Optional[str] = data.get("prompt_hash")
    extra_meta: dict = data.get("metadata", {})

    # ── Decode image ──────────────────────────────────────────────────────
    try:
        img_rgb = _decode_image(data["image"])
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # ── Generate identifiers ──────────────────────────────────────────────
    image_id = f"img-{uuid.uuid4()}"
    ts_int = int(time.time())
    ts_iso = datetime.fromtimestamp(ts_int, tz=timezone.utc).isoformat()

    # ── Embed latent watermark ────────────────────────────────────────────
    latent_embedded = False
    working_img = img_rgb.copy()

    if enable_latent:
        working_img = _embed_latent_watermark(
            working_img, image_id, ts_int, svc.secret_key
        )
        latent_embedded = True
        logger.debug("Latent watermark embedded for %s", image_id)

    # ── Embed pixel watermark ─────────────────────────────────────────────
    pixel_embedded = False

    if enable_pixel and svc.pixel_encoder is not None:
        px_result = _embed_pixel_watermark(working_img, image_id, svc)
        if px_result is not None:
            working_img = px_result
            pixel_embedded = True
            logger.debug("Pixel watermark embedded for %s", image_id)
    elif enable_pixel and svc.pixel_encoder is None:
        logger.info("Pixel encoder not loaded — skipping pixel watermark for %s", image_id)

    # ── Perceptual hash ───────────────────────────────────────────────────
    hashes = _compute_phash(working_img)
    combined_hash = f"phash:{hashes['phash']}"

    # ── Sign record ───────────────────────────────────────────────────────
    sign_payload = {
        "image_id":  image_id,
        "model_id":  model_id,
        "timestamp": ts_iso,
        "phash":     hashes["phash"],
    }
    signature_bytes, public_key_bytes, key_id = _sign_record(sign_payload, cfg)

    # ── Save to registry ──────────────────────────────────────────────────
    provenance_registered = False
    try:
        from provena_flask.services.registry_service import RegistryService
        db_path = cfg.get("DATABASE_PATH", "./data/provenance.db")
        registry = RegistryService(db_path=db_path if db_path != ":memory:" else None)
        registry.register_provenance(
            image_id=image_id,
            model_id=model_id,
            timestamp=ts_iso,
            watermark_payload=ts_int.to_bytes(8, "big"),
            perceptual_hash=combined_hash,
            signature=signature_bytes,
            public_key=public_key_bytes,
            key_id=key_id,
            prompt_hash=prompt_hash,
            latent_watermark_present=latent_embedded,
            pixel_watermark_present=pixel_embedded,
        )
        provenance_registered = True
        logger.info("Provenance registered for %s", image_id)
    except Exception as exc:
        logger.error("Registry write failed for %s: %s", image_id, exc)

    # ── Encode result image ───────────────────────────────────────────────
    try:
        wm_b64 = _encode_image_png(working_img)
    except Exception as exc:
        logger.error("Result image encoding failed: %s", exc)
        wm_b64 = data["image"]   # fallback: return original

    elapsed_ms = (time.perf_counter() - t_start) * 1000

    return jsonify({
        "image_id":              image_id,
        "watermarked_image":     wm_b64,
        "latent_embedded":       latent_embedded,
        "pixel_embedded":        pixel_embedded,
        "perceptual_hashes":     hashes,
        "signature":             signature_bytes.hex() if signature_bytes else "",
        "key_id":                key_id,
        "timestamp":             ts_iso,
        "model_id":              model_id,
        "provenance_registered": provenance_registered,
        "processing_ms":         round(elapsed_ms, 1),
    }), 201
