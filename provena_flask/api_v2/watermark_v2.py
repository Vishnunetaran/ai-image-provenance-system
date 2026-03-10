"""
PROVENA Trinity v2 – Standalone Watermark Utilities
GET  /api/v2/watermark/info    – Capabilities of loaded models
POST /api/v2/watermark/embed   – Standalone watermark embedding
POST /api/v2/watermark/extract – Standalone watermark extraction
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Optional

import numpy as np
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

watermark_v2_bp = Blueprint("watermark_v2", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v2/watermark/info
# ─────────────────────────────────────────────────────────────────────────────

@watermark_v2_bp.route("/watermark/info", methods=["GET"])
def watermark_info():
    """
    Return the current watermarking capabilities.

    Response:
    {
        "mode":             "full" | "latent_only" | "unavailable",
        "available_layers": { "latent_watermark": true, ... },
        "payload_bits":     160,
        "device":           "cpu",
        "pixel_image_size": 256
    }
    """
    svc = current_app.trinity  # type: ignore[attr-defined]
    cfg = current_app.config

    return jsonify({
        "mode":             svc.mode,
        "available_layers": svc.available_layers,
        "payload_bits":     cfg.get("LATENT_PAYLOAD_BITS", 160),
        "device":           svc.device,
        "pixel_image_size": cfg.get("PIXEL_IMAGE_SIZE", 256),
        "thresholds": {
            "watermark_confidence": cfg.get("WATERMARK_CONFIDENCE_THRESHOLD", 0.9),
            "phash_match":          cfg.get("PHASH_MATCH_THRESHOLD", 15),
            "cnn_confidence":       cfg.get("CNN_CONFIDENCE_THRESHOLD", 0.8),
            "vit_confidence":       cfg.get("VIT_CONFIDENCE_THRESHOLD", 0.8),
            "ensemble":             cfg.get("ENSEMBLE_THRESHOLD", 0.75),
        },
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v2/watermark/embed
# ─────────────────────────────────────────────────────────────────────────────

@watermark_v2_bp.route("/watermark/embed", methods=["POST"])
def embed_watermark():
    """
    Standalone watermark embedding (without provenance registration).

    Request JSON:
    {
        "image":         "<base64-encoded image>",
        "image_id":      "img-<uuid>",         # optional; generated if absent
        "type":          "latent" | "pixel" | "both"   (default: "latent")
    }

    Response JSON (200):
    {
        "image_id":            "img-...",
        "watermarked_image":   "<base64-PNG>",
        "latent_embedded":     true,
        "pixel_embedded":      false,
        "processing_ms":       12.4
    }
    """
    t_start = time.perf_counter()
    svc = current_app.trinity  # type: ignore[attr-defined]
    cfg = current_app.config

    if not request.is_json:
        return jsonify(error="Content-Type must be application/json"), 415

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    if "image" not in data:
        return jsonify(error="Missing required field: image"), 400

    wm_type: str = str(data.get("type", "latent")).lower()
    if wm_type not in ("latent", "pixel", "both"):
        return jsonify(error="type must be 'latent', 'pixel', or 'both'"), 400

    # Decode image
    try:
        import cv2
        raw = base64.b64decode(data["image"])
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        return jsonify(error=f"Image decoding failed: {exc}"), 400

    import uuid as _uuid
    image_id: str = str(data.get("image_id") or f"img-{_uuid.uuid4()}")
    ts_int = int(time.time())

    working = img_rgb.copy()
    latent_embedded = False
    pixel_embedded = False

    # Latent embed
    if wm_type in ("latent", "both"):
        try:
            from provena_flask.api_v2.register import _embed_latent_watermark
            working = _embed_latent_watermark(working, image_id, ts_int, svc.secret_key)
            latent_embedded = True
        except Exception as exc:
            logger.warning("Latent embed failed: %s", exc)

    # Pixel embed
    if wm_type in ("pixel", "both") and svc.pixel_encoder is not None:
        try:
            from provena_flask.api_v2.register import _embed_pixel_watermark
            px = _embed_pixel_watermark(working, image_id, svc)
            if px is not None:
                working = px
                pixel_embedded = True
        except Exception as exc:
            logger.warning("Pixel embed failed: %s", exc)
    elif wm_type in ("pixel", "both"):
        logger.info("Pixel encoder not loaded — skipping pixel embed")

    # Encode result
    try:
        import cv2 as _cv2
        bgr_out = _cv2.cvtColor(working, _cv2.COLOR_RGB2BGR)
        _, buf = _cv2.imencode(".png", bgr_out)
        wm_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception as exc:
        logger.error("Output encoding failed: %s", exc)
        wm_b64 = data["image"]

    return jsonify({
        "image_id":          image_id,
        "watermarked_image": wm_b64,
        "latent_embedded":   latent_embedded,
        "pixel_embedded":    pixel_embedded,
        "processing_ms":     round((time.perf_counter() - t_start) * 1000, 1),
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v2/watermark/extract
# ─────────────────────────────────────────────────────────────────────────────

@watermark_v2_bp.route("/watermark/extract", methods=["POST"])
def extract_watermark():
    """
    Standalone watermark extraction (no registry lookup).

    Request JSON:
    {
        "image":  "<base64-encoded image>",
        "type":   "latent" | "pixel" | "both"   (default: "latent")
    }

    Response JSON (200):
    {
        "latent": {
            "detected":   true,
            "confidence": 0.92,
            "image_id":   "img-...",
            "timestamp":  1700000000
        },
        "pixel": {
            "detected":   false,
            "reason":     "model not loaded"
        },
        "processing_ms": 48.3
    }
    """
    t_start = time.perf_counter()
    svc = current_app.trinity  # type: ignore[attr-defined]
    cfg = current_app.config

    if not request.is_json:
        return jsonify(error="Content-Type must be application/json"), 415

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    if "image" not in data:
        return jsonify(error="Missing required field: image"), 400

    wm_type: str = str(data.get("type", "latent")).lower()
    if wm_type not in ("latent", "pixel", "both"):
        return jsonify(error="type must be 'latent', 'pixel', or 'both'"), 400

    # Decode image
    try:
        import cv2
        raw = base64.b64decode(data["image"])
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        return jsonify(error=f"Image decoding failed: {exc}"), 400

    result: Dict[str, Any] = {}

    # ── Latent extraction ─────────────────────────────────────────────────
    if wm_type in ("latent", "both"):
        if svc.latent_extractor is not None:
            try:
                extracted, confidence = svc.latent_extractor.extract(img_rgb)
                threshold = cfg.get("WATERMARK_CONFIDENCE_THRESHOLD", 0.9)
                detected = extracted is not None and confidence >= threshold
                result["latent"] = {
                    "detected":   detected,
                    "confidence": round(float(confidence), 4),
                    "image_id":   extracted.get("image_id") if extracted else None,
                    "timestamp":  extracted.get("timestamp") if extracted else None,
                }
            except Exception as exc:
                result["latent"] = {"detected": False, "error": str(exc)}
        else:
            result["latent"] = {"detected": False, "reason": "extractor not loaded"}

    # ── Pixel extraction ──────────────────────────────────────────────────
    if wm_type in ("pixel", "both"):
        if svc.pixel_decoder is not None:
            try:
                import torch
                import torchvision.transforms.functional as TF
                from PIL import Image as PILImage

                pil = PILImage.fromarray(img_rgb).resize(
                    (cfg.get("PIXEL_IMAGE_SIZE", 256), cfg.get("PIXEL_IMAGE_SIZE", 256))
                )
                img_t = (TF.to_tensor(pil).unsqueeze(0) * 2 - 1).to(svc.device)  # type: ignore

                with torch.no_grad():
                    logits = svc.pixel_decoder(img_t)
                    bits = (logits > 0).squeeze(0).cpu().tolist()

                # Attempt payload decode
                try:
                    from src.watermarking.latent.payload import decode_payload
                    import numpy as _np
                    uuid_str, ts = decode_payload(_np.array(bits, dtype=np.uint8))
                    result["pixel"] = {
                        "detected":   uuid_str is not None,
                        "image_id":   uuid_str,
                        "timestamp":  ts,
                        "bit_accuracy": None,  # unknown without ground-truth
                    }
                except Exception:
                    result["pixel"] = {
                        "detected":   True,
                        "bits":       bits[:32],   # first 32 bits as preview
                        "note":       "Payload ECC decode failed; raw bits returned",
                    }
            except Exception as exc:
                result["pixel"] = {"detected": False, "error": str(exc)}
        else:
            result["pixel"] = {"detected": False, "reason": "decoder not loaded"}

    result["processing_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
    return jsonify(result), 200
