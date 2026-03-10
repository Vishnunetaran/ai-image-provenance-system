"""
API v2 - Image Detection Endpoint.

POST /api/v2/images/detect

Multi-layer AI image detection with configurable depth:
    - "fast": Watermark + pHash (≤50ms)
    - "standard": + CNN classifier (≤150ms)
    - "deep": + ViT detector (≤300ms)

Request JSON:
    {
        "image": "base64_encoded_image",
        "mode": "deep"
    }

Response JSON:
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
"""

import base64
import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from flask import Blueprint, jsonify, request

    detect_v2_bp = Blueprint("detect_v2", __name__)
except ImportError:
    detect_v2_bp = None

# ── Detector Singleton ────────────────────────────────────────────────

_detector = None


def _get_detector():
    """Get or create the TrinityDetector singleton."""
    global _detector
    if _detector is None:
        try:
            from src.detection.ensemble import TrinityDetector

            secret_key = os.environ.get("PROVENA_SECRET_KEY", "\x00" * 32).encode("utf-8")[:32].ljust(32, b"\x00")

            _detector = TrinityDetector(
                secret_key=secret_key,
                cnn_model_path=os.environ.get("CNN_MODEL_PATH"),
                vit_model_path=os.environ.get("VIT_MODEL_PATH"),
            )
        except Exception as e:
            logger.error(f"Failed to initialize TrinityDetector: {e}")
            raise

    return _detector


def _decode_image(b64_string: str) -> np.ndarray:
    """Decode base64 image to numpy array."""
    img_bytes = base64.b64decode(b64_string)
    img_array = np.frombuffer(img_bytes, dtype=np.uint8)
    return cv2.imdecode(img_array, cv2.IMREAD_COLOR)


def detect_image_v2():
    """Detect whether an image is AI-generated using Trinity ensemble.

    Core detection handler for the v2 API.
    """
    data = request.get_json() if request else {}

    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field"}), 400

    try:
        # Decode image
        image = _decode_image(data["image"])
        if image is None:
            return jsonify({"error": "Invalid image data"}), 400

        mode = data.get("mode", "standard")
        if mode not in ("fast", "standard", "deep"):
            return jsonify({"error": "Invalid mode. Use: fast, standard, deep"}), 400

        candidate_ids = data.get("candidate_ids", None)

        # Run detection
        detector = _get_detector()
        result = detector.detect(
            image=image,
            mode=mode,
            candidate_ids=candidate_ids,
        )

        # Convert to JSON response
        response = detector.to_dict(result)
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Detection failed: {e}", exc_info=True)
        return jsonify({"error": f"Detection failed: {str(e)}"}), 500


# ── Flask Route Registration ─────────────────────────────────────────

if detect_v2_bp is not None:

    @detect_v2_bp.route("/images/detect", methods=["POST"])
    def detect_endpoint():
        """POST /api/v2/images/detect"""
        return detect_image_v2()
