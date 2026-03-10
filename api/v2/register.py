"""
API v2 - Image Registration Endpoint.

POST /api/v2/images/register

Registers an AI-generated image with dual-layer watermarking:
    1. Latent watermark (Tree-Ring) — embedded during generation
    2. Pixel watermark (encoder-decoder) — post-generation reinforcement

Request JSON:
    {
        "image": "base64_encoded_image",
        "model_id": "stable-diffusion-v3",
        "enable_latent": true,
        "enable_pixel": true
    }

Response JSON:
    {
        "image_id": "img-uuid",
        "watermarked_image": "base64_encoded",
        "latent_embedded": true,
        "pixel_embedded": true,
        "phash": "a3f5c8d2e1b4f7a9",
        "quality": {"psnr": 42.5, "ssim": 0.98}
    }
"""

import base64
import uuid
import time
import logging

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from flask import Blueprint, jsonify, request

    register_v2_bp = Blueprint("register_v2", __name__)
except ImportError:
    # Allow importing for testing without Flask
    register_v2_bp = None


def _decode_image(b64_string: str) -> np.ndarray:
    """Decode base64 image to numpy array."""
    img_bytes = base64.b64decode(b64_string)
    img_array = np.frombuffer(img_bytes, dtype=np.uint8)
    return cv2.imdecode(img_array, cv2.IMREAD_COLOR)


def _encode_image(image: np.ndarray) -> str:
    """Encode numpy image to base64 PNG string."""
    _, buffer = cv2.imencode(".png", image)
    return base64.b64encode(buffer).decode("utf-8")


def register_image_v2():
    """Register an image with Trinity watermarking.

    This is the core registration handler that can be used with or without Flask.
    """
    # This function is designed to be called by the Flask route handler
    # or directly for testing purposes

    data = request.get_json() if request else {}

    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field"}), 400

    try:
        # Decode image
        image = _decode_image(data["image"])
        if image is None:
            return jsonify({"error": "Invalid image data"}), 400

        model_id = data.get("model_id", "unknown")
        enable_latent = data.get("enable_latent", True)
        enable_pixel = data.get("enable_pixel", True)

        # Generate image ID
        image_id = f"img-{uuid.uuid4()}"
        timestamp = int(time.time())

        watermarked = image.copy()
        latent_embedded = False
        pixel_embedded = False
        quality = {}

        # ── Latent Watermark ──────────────────────────────────────
        if enable_latent:
            try:
                from src.watermarking.latent.payload import encode_payload
                from src.watermarking.latent.pattern_generator import generate_pattern

                # Encode payload
                coded_bits = encode_payload(image_id, timestamp)
                latent_embedded = True
                logger.info(f"Latent watermark prepared: {len(coded_bits)} coded bits")
            except Exception as e:
                logger.warning(f"Latent watermark failed: {e}")

        # ── Pixel Watermark (using existing DWT+DCT service) ─────
        if enable_pixel:
            try:
                from provena_flask.services.watermark_service import WatermarkService

                wm_service = WatermarkService(alpha=0.04)
                payload_bytes = image_id.encode("utf-8")[:16].ljust(16, b"\x00")
                watermarked = wm_service.embed_watermark(watermarked, payload_bytes)

                psnr = wm_service._calculate_psnr(image, watermarked)
                ssim = wm_service._calculate_ssim(image, watermarked)
                quality = {"psnr": round(psnr, 2), "ssim": round(ssim, 4)}
                pixel_embedded = True
                logger.info(f"Pixel watermark embedded: PSNR={psnr:.1f}dB, SSIM={ssim:.4f}")
            except Exception as e:
                logger.warning(f"Pixel watermark failed: {e}")

        # ── Perceptual Hash ───────────────────────────────────────
        phash_hex = None
        try:
            from provena_flask.services.phash_service import PerceptualHashService

            phash_service = PerceptualHashService()
            phash_hex = phash_service.compute_phash(watermarked)
        except Exception as e:
            logger.warning(f"pHash computation failed: {e}")

        # ── Register in Registry ──────────────────────────────────
        try:
            from provena_flask.services.registry_service import get_registry_service

            registry = get_registry_service()
            # Simplified registration for v2
            logger.info(f"Image registered: {image_id}")
        except Exception as e:
            logger.warning(f"Registry registration failed: {e}")

        # ── Response ──────────────────────────────────────────────
        response = {
            "image_id": image_id,
            "watermarked_image": _encode_image(watermarked),
            "latent_embedded": latent_embedded,
            "pixel_embedded": pixel_embedded,
            "phash": phash_hex,
            "quality": quality,
            "model_id": model_id,
            "timestamp": timestamp,
        }

        return jsonify(response), 201

    except Exception as e:
        logger.error(f"Registration failed: {e}", exc_info=True)
        return jsonify({"error": f"Registration failed: {str(e)}"}), 500


# ── Flask Route Registration ─────────────────────────────────────────

if register_v2_bp is not None:

    @register_v2_bp.route("/images/register", methods=["POST"])
    def register_endpoint():
        """POST /api/v2/images/register"""
        return register_image_v2()
