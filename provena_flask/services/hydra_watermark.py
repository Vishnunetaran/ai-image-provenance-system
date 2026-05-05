"""
HydraWatermark — Multi-layer watermark orchestrator.

Coordinates 3 independent watermark layers that operate in different
mathematical domains:
  1. Neural (TrustMark) — learned encoder/decoder, robust to compression
  2. DCT (frequency)    — coefficient modulation, robust to spatial attacks
  3. Spatial (LSB+ECC)  — pixel-level embedding, robust to frequency attacks

An attacker must independently defeat ALL THREE layers to strip the watermark.

Public API:
    embed(image, payload)  -> watermarked PIL Image
    extract(image)         -> VoteResult (payload + confidence + breakdown)
"""
from __future__ import annotations

import logging
import time
from PIL import Image

from provena_flask.services.layers import neural_layer, dct_layer, spatial_layer
from provena_flask.services.majority_vote import vote, VoteResult

logger = logging.getLogger(__name__)

# Ordered list of layers — embedding happens in this order
LAYERS = [
    ("neural_trustmark", neural_layer),
    ("dct_frequency", dct_layer),
    ("spatial_lsb", spatial_layer),
]


def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed payload into image using all 3 watermark layers sequentially.

    Each layer modifies the image independently in its own domain.
    Neural operates on learned feature space, DCT on frequency coefficients,
    Spatial on pixel LSBs. The signals are additive and do not interfere
    significantly because they target different aspects of the image.

    Args:
        image:   PIL Image (RGB or RGBA). Min 256x256 for neural, 64x64 for others.
        payload: Exactly 6 bytes (48 bits) of provenance data.

    Returns:
        Watermarked PIL Image with all 3 layers embedded.

    Raises:
        ValueError: If payload is not exactly 6 bytes.
    """
    if len(payload) != 6:
        raise ValueError(f"HydraWatermark expects 6-byte payload, got {len(payload)}")

    result_img = image.convert("RGB")
    results = {}
    t0 = time.time()

    for layer_name, layer_module in LAYERS:
        try:
            t_layer = time.time()
            result_img = layer_module.embed(result_img, payload)
            elapsed = time.time() - t_layer
            results[layer_name] = {"status": "OK", "time_ms": round(elapsed * 1000)}
            logger.info("HydraWatermark: %s embed OK (%.0f ms)", layer_name, elapsed * 1000)
        except Exception as exc:
            results[layer_name] = {"status": "FAILED", "error": str(exc)[:100]}
            logger.warning("HydraWatermark: %s embed FAILED: %s", layer_name, exc)
            # Continue with remaining layers — partial embedding is better than none

    total_ms = round((time.time() - t0) * 1000)
    ok_count = sum(1 for v in results.values() if v["status"] == "OK")

    logger.info(
        "HydraWatermark embed complete: %d/%d layers OK, total %.0f ms",
        ok_count, len(LAYERS), total_ms,
    )

    return result_img


def extract(image: Image.Image, min_confidence: float = 0.5) -> VoteResult:
    """
    Extract payload from image using all 3 layers, then majority-vote.

    Each layer independently attempts extraction. Results are aggregated
    by majority_vote to determine the winning payload.

    Args:
        image:          PIL Image to extract from.
        min_confidence: Minimum per-layer confidence to count as a vote.

    Returns:
        VoteResult with winning payload, confidence, and per-layer breakdown.
    """
    img_rgb = image.convert("RGB")
    extraction_results = []
    t0 = time.time()

    for layer_name, layer_module in LAYERS:
        try:
            t_layer = time.time()
            payload_bytes, confidence = layer_module.extract(img_rgb)
            elapsed = time.time() - t_layer
            extraction_results.append((layer_name, payload_bytes, confidence))
            logger.info(
                "HydraWatermark: %s extract -> %s (conf=%.3f, %.0f ms)",
                layer_name,
                payload_bytes.hex() if payload_bytes else "empty",
                confidence,
                elapsed * 1000,
            )
        except Exception as exc:
            extraction_results.append((layer_name, b"", 0.0))
            logger.warning("HydraWatermark: %s extract FAILED: %s", layer_name, exc)

    total_ms = round((time.time() - t0) * 1000)
    result = vote(extraction_results, min_confidence=min_confidence)

    logger.info(
        "HydraWatermark extract complete: winner=%s, votes=%d/%d, conf=%.3f (%.0f ms)",
        result.payload.hex() if result.payload else "none",
        result.winner_votes,
        result.total_votes,
        result.confidence,
        total_ms,
    )

    return result
