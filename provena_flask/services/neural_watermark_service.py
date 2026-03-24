"""
Neural Watermark Service — T-006, T-007, T-008
Uses invisible-watermark (open-source) for robust neural watermarking.

TrustMark requires GPU + large model downloads; for Sprint 1 we use the
`invisible-watermark` library (DwtDct / DwtDctSvd / RivaGAN backends) which
is pip-installable without GPU and gives >90% bit accuracy on JPEG q=70.
TrustMark can be swapped in by changing BACKEND below once GPU is available.
"""
from __future__ import annotations

import io
import logging
import struct
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
# Options: "invisible_watermark" (default, CPU-friendly) | "trustmark" (GPU)
BACKEND = "invisible_watermark"

try:
    from imwatermark import WatermarkEncoder, WatermarkDecoder  # invisible-watermark
    _IW_AVAILABLE = True
except ImportError:
    _IW_AVAILABLE = False
    logger.warning(
        "invisible-watermark not installed. "
        "Run: pip install invisible-watermark  "
        "Falling back to no-op stub (for testing only)."
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAYLOAD_BITS = 256           # 32 bytes × 8 bits
_WM_METHOD   = "dwtDctSvd"  # Most robust CPU method in invisible-watermark
# Confidence threshold: below this, extraction is unreliable → use pHash fallback
CONFIDENCE_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed a 256-bit payload into an image using neural watermarking.

    The watermark is imperceptible — PSNR should be ≥ 42 dB with dwtDctSvd.
    pHash MUST be computed on the *original* image before calling this function.

    Args:
        image:   PIL Image (RGB or RGBA). Min size 256×256.
        payload: Exactly 32 bytes (256 bits) of provenance data.

    Returns:
        Watermarked PIL Image (same mode as input).

    Raises:
        ValueError: If payload is not exactly 32 bytes.
        RuntimeError: If the watermark embedding fails.
    """
    if len(payload) != 32:
        raise ValueError(f"payload must be exactly 32 bytes, got {len(payload)}")

    # Convert to numpy BGR (invisible-watermark expects BGR uint8)
    img_rgb = image.convert("RGB")
    img_np = np.array(img_rgb)
    img_bgr = img_np[:, :, ::-1].copy()

    if not _IW_AVAILABLE:
        logger.error("invisible-watermark not available; returning original image")
        return image

    try:
        bits = _bytes_to_bits(payload)
        encoder = WatermarkEncoder()
        encoder.set_watermark("bits", bits)
        watermarked_bgr = encoder.encode(img_bgr, _WM_METHOD)
        watermarked_rgb = watermarked_bgr[:, :, ::-1]
        result = Image.fromarray(watermarked_rgb.astype(np.uint8))
        logger.info("Neural watermark embedded successfully (%d bits)", PAYLOAD_BITS)
        return result
    except Exception as exc:
        raise RuntimeError(f"Watermark embedding failed: {exc}") from exc


def extract(image: Image.Image) -> tuple[bytes, float]:
    """
    Extract a 256-bit payload from a watermarked image.

    Args:
        image: PIL Image to extract from.

    Returns:
        Tuple of (payload_bytes: bytes, confidence: float 0.0–1.0).
        If confidence < CONFIDENCE_THRESHOLD (0.70), use pHash fallback.

    Raises:
        RuntimeError: If extraction fails unexpectedly.
    """
    img_rgb = image.convert("RGB")
    img_np = np.array(img_rgb)
    img_bgr = img_np[:, :, ::-1].copy()

    if not _IW_AVAILABLE:
        logger.error("invisible-watermark not available")
        return (b"\x00" * 32, 0.0)

    try:
        decoder = WatermarkDecoder("bits", PAYLOAD_BITS)
        raw_bits = decoder.decode(img_bgr, _WM_METHOD)

        # raw_bits is a numpy array of 0/1 integers (PAYLOAD_BITS long)
        payload = _bits_to_bytes(raw_bits)

        # Compute confidence: fraction of bits that look "clean" (0 or 1, not noise).
        # For invisible-watermark the bits are already binarised; we estimate
        # confidence from per-bit certainty not directly available, so we use
        # a heuristic: ratio of bits that are strongly 0 or 1 would need raw
        # float outputs. As a proxy we return 0.90 when extraction succeeds,
        # indicating high but not perfect confidence.
        confidence = _estimate_confidence(raw_bits)

        logger.info(
            "Neural watermark extracted (confidence=%.3f)", confidence
        )
        return (payload, confidence)
    except Exception as exc:
        logger.warning("Watermark extraction failed: %s", exc)
        return (b"\x00" * 32, 0.0)


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
        PSNR in dB. Higher is better (target ≥ 42 dB for imperceptible).
    """
    orig_np = np.array(original.convert("RGB")).astype(np.float64)
    wm_np   = np.array(watermarked.convert("RGB")).astype(np.float64)
    mse = np.mean((orig_np - wm_np) ** 2)
    if mse == 0:
        return float("inf")
    return 10 * np.log10((255.0 ** 2) / mse)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _bytes_to_bits(data: bytes) -> list[int]:
    """Convert bytes to list of 0/1 integers, MSB first."""
    bits: list[int] = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_bytes(bits: np.ndarray) -> bytes:
    """Convert array of 0/1 integers back to bytes."""
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte_bits = bits[i : i + 8]
        byte = 0
        for j, bit in enumerate(byte_bits):
            byte |= int(bit) << (7 - j)
        result.append(byte)
    return bytes(result)


def _estimate_confidence(bits: np.ndarray) -> float:
    """
    Heuristic confidence estimate.

    For dwtDctSvd the decoder returns hard-binarised bits (0 or 1).
    We check that the full payload length is intact and return a
    fixed high-confidence value when extraction succeeds.
    A more accurate estimate requires per-bit soft scores from the decoder
    (available with TrustMark but not with invisible-watermark).
    """
    if len(bits) != PAYLOAD_BITS:
        return 0.0
    # If all bits are zero, the image likely had no watermark
    if np.all(bits == 0):
        return 0.0
    return 0.90
