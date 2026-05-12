"""
Neural Layer — TrustMark wrapper for HydraWatermark.

Thin adapter around the existing neural_watermark_service to conform
to the unified layer interface: embed(image, payload) / extract(image).
"""
from __future__ import annotations

import logging
from PIL import Image
from provena_flask.services import neural_watermark_service

logger = logging.getLogger(__name__)
LAYER_NAME = "neural_trustmark"


def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """Embed payload using TrustMark neural network."""
    return neural_watermark_service.embed(image, payload)


def extract(image: Image.Image) -> tuple[bytes, float]:
    """Extract payload using TrustMark neural network.
    Returns (payload_bytes, confidence).
    """
    return neural_watermark_service.extract(image)
