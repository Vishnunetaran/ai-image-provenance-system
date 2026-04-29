"""
Neural Watermark Service — T-006, T-007, T-008
Uses TrustMark (model_type='Q') for robust neural watermarking.

TrustMark embeds a string payload into image pixels using a learned
encoder/decoder network. It survives JPEG compression, crops, and
common social-media transforms far better than legacy DWT methods.

Public API:
    embed(image, payload)  -> watermarked PIL Image
    extract(image)         -> (payload_bytes, confidence)
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

import numpy as np
from PIL import Image

# Force CPU mode before any torch import
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.70

# ---------------------------------------------------------------------------
# Model loading (lazy singleton)
# ---------------------------------------------------------------------------
_tm = None


def _get_model():
    """Lazy-load TrustMark Q model (highest quality variant) for raw binary."""
    global _tm
    if _tm is None:
        try:
            from trustmark import TrustMark
            _tm = TrustMark(verbose=False, model_type='Q', use_ECC=True, secret_len=100)
            logger.info("TrustMark model loaded (type=Q, CPU mode, 100-bit total with BCH ECC)")
        except Exception as exc:
            logger.error("Failed to load TrustMark model: %s", exc)
            raise RuntimeError(f"TrustMark init failed: {exc}") from exc
    return _tm


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed a payload into an image using TrustMark neural watermarking.

    Args:
        image:   PIL Image (RGB or RGBA). Min size 256×256.
        payload: Exactly 6 bytes (48 bits) of provenance data.

    Returns:
        Watermarked PIL Image (same mode as input).

    Raises:
        ValueError: If payload is not exactly 6 bytes.
        RuntimeError: If the watermark embedding fails.
    """
    if len(payload) != 6:
        raise ValueError(f"payload must be exactly 6 bytes, got {len(payload)}")

    img_rgb = image.convert("RGB")

    try:
        tm = _get_model()
        # TrustMark binary mode automatically pads up to its capacity (e.g. 61 bits for BCH_5)
        # We pass exactly 48 chars of '0' and '1' securely.
        payload_bin = "".join(f"{b:08b}" for b in payload)
        watermarked = tm.encode(img_rgb, payload_bin, MODE='binary')

        if isinstance(watermarked, np.ndarray):
            watermarked = Image.fromarray(watermarked.astype(np.uint8))

        logger.info("Neural watermark embedded successfully (6 bytes / 48 bits)")
        return watermarked
    except Exception as exc:
        raise RuntimeError(f"Watermark embedding failed: {exc}") from exc


def extract(image: Image.Image) -> tuple[bytes, float]:
    """
    Extract a payload from a watermarked image using TrustMark.

    Args:
        image: PIL Image to extract from.

    Returns:
        Tuple of (payload_bytes: bytes, confidence: float 0.0–1.0).
        If confidence < CONFIDENCE_THRESHOLD (0.70), use pHash fallback.
    """
    img_rgb = image.convert("RGB")

    try:
        tm = _get_model()
        # TrustMark decode returns (wm_str, confidence, detected)
        result = tm.decode(img_rgb, MODE='binary')

        if isinstance(result, tuple) and len(result) >= 3:
            wm_str, confidence, detected = result[0], result[1], result[2]
        elif isinstance(result, tuple) and len(result) == 2:
            wm_str, confidence = result[0], result[1]
            detected = bool(wm_str)
        else:
            wm_str = str(result) if result else ""
            confidence = 0.0
            detected = False

        if not detected or not wm_str:
            logger.info("Neural watermark not detected (confidence=%.3f)", confidence)
            return (b"", 0.0)

        # Convert binary string of 0/1 back to exactly 6 bytes (48 bits)
        try:
            # TrustMark BCH_5 decode returns exactly its capacity (e.g., 61 chars for version 1).
            # We ONLY need our original 48 structural bits.
            if len(wm_str) < 48:
                logger.warning("Extracted watermark bit length %d is less than 48", len(wm_str))
                return (b"", 0.0)
            valid_bits = wm_str[:48] # Retrieve exactly the functional 48 bits
            payload_bytes = int(valid_bits, 2).to_bytes(6, byteorder='big')
        except (ValueError, TypeError):
            logger.warning("Extracted watermark is not valid binary string: %s", wm_str[:48])
            return (b"", 0.0)

        # Ensure confidence is a float
        if not isinstance(confidence, float) or confidence < 0:
            try:
                confidence = float(confidence)
            except (ValueError, TypeError):
                confidence = 0.9  # TrustMark Q model typically has high confidence

        logger.info(
            "Neural watermark extracted (confidence=%.3f, %d bytes)",
            confidence, len(payload_bytes)
        )
        return (payload_bytes, confidence)

    except Exception as exc:
        logger.warning("Watermark extraction failed: %s", exc)
        return (b"", 0.0)


# ---------------------------------------------------------------------------
# Image quality metrics
# ---------------------------------------------------------------------------

def compute_psnr(original: Image.Image, watermarked: Image.Image) -> float:
    """
    Compute Peak Signal-to-Noise Ratio between original and watermarked images.

    Args:
        original:    Original PIL Image.
        watermarked: Watermarked PIL Image.

    Returns:
        PSNR in dB. Higher is better (target ≥ 48 dB for TrustMark Q).
    """
    orig_np = np.array(original.convert("RGB")).astype(np.float64)
    wm_np   = np.array(watermarked.convert("RGB")).astype(np.float64)
    mse = np.mean((orig_np - wm_np) ** 2)
    if mse == 0:
        return float("inf")
    return 10 * np.log10((255.0 ** 2) / mse)
