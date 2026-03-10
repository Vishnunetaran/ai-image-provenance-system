"""
PROVENA Trinity v2 – Image Detection Endpoint
POST /api/v2/images/detect

Runs the 4-layer Trinity detection ensemble against a query image and
returns a structured verdict.  Each layer is attempted independently;
missing model weights are handled with graceful degradation.

Detection modes:
  fast     – Layers 1 (latent) + 2 (pHash)           ~1–50 ms
  standard – + Layer 3 (CNN classifier)               ~150 ms
  deep     – + Layer 4 (ViT detector)                 ~300 ms
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

detect_bp = Blueprint("detect_v2", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Layer result schema
# ─────────────────────────────────────────────────────────────────────────────

def _layer_result(
    name: str,
    ran: bool,
    detected: bool,
    confidence: float,
    image_id: Optional[str] = None,
    details: Optional[Dict] = None,
) -> Dict[str, Any]:
    return {
        "layer":      name,
        "ran":        ran,
        "detected":   detected,
        "confidence": round(confidence, 4),
        "image_id":   image_id,
        "details":    details or {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Individual layer runners
# ─────────────────────────────────────────────────────────────────────────────

def _run_latent_layer(img_rgb: np.ndarray, svc, cfg) -> Dict[str, Any]:
    """Layer 1: Latent watermark extraction via correlation."""
    t0 = time.perf_counter()
    if svc.latent_extractor is None:
        return _layer_result("latent_watermark", ran=False, detected=False, confidence=0.0,
                             details={"reason": "extractor not loaded"})
    try:
        result, confidence = svc.latent_extractor.extract(img_rgb)
        detected = result is not None and confidence >= cfg.get("WATERMARK_CONFIDENCE_THRESHOLD", 0.9)
        image_id = result.get("image_id") if result else None
        return _layer_result(
            "latent_watermark",
            ran=True,
            detected=detected,
            confidence=float(confidence),
            image_id=image_id,
            details={"latency_ms": round((time.perf_counter() - t0) * 1000, 1)},
        )
    except Exception as exc:
        logger.warning("Latent layer failed: %s", exc)
        return _layer_result("latent_watermark", ran=True, detected=False, confidence=0.0,
                             details={"error": str(exc)})


def _run_phash_layer(img_rgb: np.ndarray, cfg) -> Dict[str, Any]:
    """Layer 2: Perceptual-hash registry lookup."""
    t0 = time.perf_counter()
    try:
        from PIL import Image as PILImage
        import imagehash
        from provena_flask.services.registry_service import RegistryService

        pil = PILImage.fromarray(img_rgb)
        query_phash = str(imagehash.phash(pil))

        db_path = cfg.get("DATABASE_PATH", "./data/provenance.db")
        if db_path == ":memory:":
            # Tests with in-memory DB — skip DB lookup
            return _layer_result("perceptual_hash", ran=True, detected=False, confidence=0.0,
                                 details={"reason": "in-memory db"})

        registry = RegistryService(db_path=db_path)
        candidates = registry.search_by_perceptual_hash(f"phash:{query_phash}")

        threshold = cfg.get("PHASH_MATCH_THRESHOLD", 15)
        best_match = None
        best_distance = threshold + 1

        for rec in candidates:
            stored = rec.get("perceptual_hash", "")
            if stored.startswith("phash:"):
                stored = stored[6:]
            try:
                dist = imagehash.hex_to_hash(query_phash) - imagehash.hex_to_hash(stored)
                if dist < best_distance:
                    best_distance = dist
                    best_match = rec
            except Exception:
                continue

        detected = best_match is not None and best_distance <= threshold
        # Confidence: 1.0 at distance 0, 0.0 at threshold
        confidence = max(0.0, 1.0 - best_distance / (threshold + 1)) if detected else 0.0
        image_id = best_match["image_id"] if best_match else None

        return _layer_result(
            "perceptual_hash",
            ran=True,
            detected=detected,
            confidence=confidence,
            image_id=image_id,
            details={
                "query_phash":    query_phash,
                "best_distance":  best_distance,
                "candidates":     len(candidates),
                "latency_ms":     round((time.perf_counter() - t0) * 1000, 1),
            },
        )
    except Exception as exc:
        logger.warning("pHash layer failed: %s", exc)
        return _layer_result("perceptual_hash", ran=True, detected=False, confidence=0.0,
                             details={"error": str(exc)})


def _run_cnn_layer(img_rgb: np.ndarray, svc, cfg) -> Dict[str, Any]:
    """Layer 3: CNN-based AI-image style classifier."""
    t0 = time.perf_counter()
    if svc.cnn_classifier is None:
        return _layer_result("cnn_classifier", ran=False, detected=False, confidence=0.0,
                             details={"reason": "model not loaded"})
    try:
        detected, confidence = svc.cnn_classifier.predict(img_rgb)
        return _layer_result(
            "cnn_classifier",
            ran=True,
            detected=bool(detected),
            confidence=float(confidence),
            details={"latency_ms": round((time.perf_counter() - t0) * 1000, 1)},
        )
    except Exception as exc:
        logger.warning("CNN layer failed: %s", exc)
        return _layer_result("cnn_classifier", ran=True, detected=False, confidence=0.0,
                             details={"error": str(exc)})


def _run_vit_layer(img_rgb: np.ndarray, svc, cfg) -> Dict[str, Any]:
    """Layer 4: ViT global-pattern detector."""
    t0 = time.perf_counter()
    if svc.vit_detector is None:
        return _layer_result("vit_detector", ran=False, detected=False, confidence=0.0,
                             details={"reason": "model not loaded"})
    try:
        detected, confidence = svc.vit_detector.predict(img_rgb)
        return _layer_result(
            "vit_detector",
            ran=True,
            detected=bool(detected),
            confidence=float(confidence),
            details={"latency_ms": round((time.perf_counter() - t0) * 1000, 1)},
        )
    except Exception as exc:
        logger.warning("ViT layer failed: %s", exc)
        return _layer_result("vit_detector", ran=True, detected=False, confidence=0.0,
                             details={"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble voting
# ─────────────────────────────────────────────────────────────────────────────

LAYER_ORDER = ["latent_watermark", "perceptual_hash", "cnn_classifier", "vit_detector"]

DEFAULT_WEIGHTS = {
    "latent_watermark": 0.40,
    "perceptual_hash":  0.30,
    "cnn_classifier":   0.20,
    "vit_detector":     0.10,
}


def _ensemble_verdict(
    layer_results: List[Dict[str, Any]],
    cfg,
) -> Dict[str, Any]:
    """
    Compute weighted ensemble score and map to status string.

    Status thresholds (configurable via ENSEMBLE_THRESHOLD):
      ≥ 0.95  → VERIFIED
      ≥ 0.75  → LIKELY_VERIFIED
      ≥ 0.50  → SUSPICIOUS
      <  0.50 → NOT_DETECTED
    """
    weights: Dict[str, float] = cfg.get("ENSEMBLE_WEIGHTS", DEFAULT_WEIGHTS)
    threshold: float = cfg.get("ENSEMBLE_THRESHOLD", 0.75)

    total_weight = 0.0
    weighted_score = 0.0
    detected_layers: List[str] = []
    best_image_id: Optional[str] = None

    for r in layer_results:
        if not r["ran"]:
            continue
        name = r["layer"]
        w = weights.get(name, 0.0)
        total_weight += w
        if r["detected"]:
            weighted_score += w * r["confidence"]
            detected_layers.append(name)
            if r.get("image_id") and best_image_id is None:
                best_image_id = r["image_id"]

    # Normalise to [0, 1] accounting for missing layers
    if total_weight > 0:
        final_score = weighted_score / total_weight
    else:
        final_score = 0.0

    # Status mapping
    if final_score >= 0.95:
        status = "VERIFIED"
    elif final_score >= max(threshold, 0.75):
        status = "LIKELY_VERIFIED"
    elif final_score >= 0.50:
        status = "SUSPICIOUS"
    else:
        status = "NOT_DETECTED"

    return {
        "status":           status,
        "confidence":       round(final_score, 4),
        "detected_layers":  detected_layers,
        "image_id":         best_image_id,
        "total_weight_ran": round(total_weight, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

VALID_MODES = {"fast", "standard", "deep"}


@detect_bp.route("/images/detect", methods=["POST"])
def detect_image():
    """
    Detect whether an image was generated (and watermarked) by PROVENA Trinity.

    Request JSON:
    {
        "image":  "<base64-encoded image>",
        "mode":   "fast" | "standard" | "deep"   (default: "standard")
    }

    Response JSON (200):
    {
        "status":      "VERIFIED" | "LIKELY_VERIFIED" | "SUSPICIOUS" | "NOT_DETECTED",
        "confidence":   0.97,
        "image_id":     "img-<uuid>",       # null if not found
        "mode":        "standard",
        "layers": [
            { "layer": "latent_watermark", "ran": true, "detected": true, "confidence": 0.95, ... },
            { "layer": "perceptual_hash",  "ran": true, "detected": true, "confidence": 0.90, ... },
            { "layer": "cnn_classifier",   "ran": true, "detected": false, "confidence": 0.43, ... },
            { "layer": "vit_detector",     "ran": false, ... }
        ],
        "processing_ms": 145.2
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

    mode: str = str(data.get("mode", "standard")).lower()
    if mode not in VALID_MODES:
        return jsonify(
            error=f"Invalid mode '{mode}'. Must be one of: {sorted(VALID_MODES)}"
        ), 400

    # ── Decode image ──────────────────────────────────────────────────────
    try:
        import cv2
        raw = base64.b64decode(data["image"])
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("cv2 could not decode image")
        img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        return jsonify(error=f"Image decoding failed: {exc}"), 400

    # ── Run detection layers by mode ──────────────────────────────────────
    layer_results: List[Dict[str, Any]] = []

    # Layer 1 – always
    layer_results.append(_run_latent_layer(img_rgb, svc, cfg))

    # Layer 2 – always
    layer_results.append(_run_phash_layer(img_rgb, cfg))

    # Layer 3 – standard + deep
    if mode in ("standard", "deep"):
        layer_results.append(_run_cnn_layer(img_rgb, svc, cfg))
    else:
        layer_results.append(_layer_result(
            "cnn_classifier", ran=False, detected=False, confidence=0.0,
            details={"reason": "mode=fast skips CNN"},
        ))

    # Layer 4 – deep only
    if mode == "deep":
        layer_results.append(_run_vit_layer(img_rgb, svc, cfg))
    else:
        layer_results.append(_layer_result(
            "vit_detector", ran=False, detected=False, confidence=0.0,
            details={"reason": f"mode={mode} skips ViT"},
        ))

    # ── Ensemble verdict ──────────────────────────────────────────────────
    verdict = _ensemble_verdict(layer_results, cfg)

    elapsed_ms = (time.perf_counter() - t_start) * 1000

    logger.info(
        "Detection complete | image=%s mode=%s status=%s confidence=%.3f elapsed=%.1fms",
        verdict.get("image_id", "N/A"),
        mode,
        verdict["status"],
        verdict["confidence"],
        elapsed_ms,
    )

    return jsonify({
        "status":         verdict["status"],
        "confidence":     verdict["confidence"],
        "image_id":       verdict.get("image_id"),
        "detected_layers": verdict["detected_layers"],
        "mode":           mode,
        "layers":         layer_results,
        "processing_ms":  round(elapsed_ms, 1),
    }), 200
