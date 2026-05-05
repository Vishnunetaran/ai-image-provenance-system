"""
Spatial Layer — Spatial-domain ECC watermarking for HydraWatermark.

Embeds payload bits into the LSBs of deterministically-selected pixel
blocks using a seeded PRNG based on image dimensions. Uses repetition
coding (majority vote per bit) for error correction.

This layer is robust against DCT/frequency-domain attacks (JPEG, DCT
stripping) because it operates purely in the spatial domain. It is
weaker against spatial filtering (blur, median filter).

Strategy:
- Select blocks deterministically using image dimensions as seed
  (same blocks on embed and extract, regardless of pixel content)
- Embed each payload bit into multiple blocks (repetition factor)
- On extraction, majority-vote across repetitions per bit
"""
from __future__ import annotations

import hashlib
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)
LAYER_NAME = "spatial_lsb"

# Payload size
PAYLOAD_BYTES = 6
PAYLOAD_BITS = PAYLOAD_BYTES * 8  # 48

# Each payload bit is embedded REPS times for error correction
REPS = 7

# Total pixels needed = 48 * 7 = 336
PIXELS_NEEDED = PAYLOAD_BITS * REPS

# Minimum image dimension
MIN_DIM = 64


def _payload_to_bits(payload: bytes) -> list[int]:
    """Convert 6-byte payload to list of 48 bits."""
    bits = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_payload(bits: list[int]) -> bytes:
    """Convert list of 48 bits to 6 bytes."""
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte_bits = bits[i:i + 8]
        if len(byte_bits) < 8:
            byte_bits.extend([0] * (8 - len(byte_bits)))
        byte_val = 0
        for b in byte_bits:
            byte_val = (byte_val << 1) | (b & 1)
        result.append(byte_val)
    return bytes(result)


def _get_pixel_positions(h: int, w: int, count: int) -> list[tuple[int, int]]:
    """
    Deterministically select pixel positions using a seeded PRNG.
    
    The seed is derived from image dimensions only (not pixel content),
    so embed and extract always select the same positions regardless
    of any pixel modifications from other watermark layers.
    """
    # Seed from dimensions — deterministic for same-size images
    seed = int(hashlib.sha256(f"provena_spatial_{h}x{w}".encode()).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)

    # Margin: avoid edges (2px border)
    margin = 2
    usable_h = h - 2 * margin
    usable_w = w - 2 * margin

    if usable_h * usable_w < count:
        raise ValueError(f"Image too small: {w}x{h}, need at least {count} usable pixels")

    # Generate unique random positions within the usable area
    all_indices = rng.permutation(usable_h * usable_w)[:count]
    positions = []
    for idx in all_indices:
        y = margin + idx // usable_w
        x = margin + idx % usable_w
        positions.append((int(y), int(x)))

    return positions


def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed payload into LSBs of deterministically-selected pixels.

    Args:
        image:   PIL Image (RGB). Min 64x64.
        payload: Exactly 6 bytes.

    Returns:
        Watermarked PIL Image.
    """
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError(f"Spatial layer expects {PAYLOAD_BYTES} bytes, got {len(payload)}")

    img_array = np.array(image.convert("RGB"), dtype=np.uint8).copy()
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        raise ValueError(f"Image too small for spatial layer: {w}x{h}")

    bits = _payload_to_bits(payload)
    positions = _get_pixel_positions(h, w, PIXELS_NEEDED)

    # Embed: for each payload bit, write it into REPS pixels
    for bit_idx, bit in enumerate(bits):
        for rep in range(REPS):
            px_idx = bit_idx * REPS + rep
            py, px = positions[px_idx]

            # Modify the LSB of the blue channel
            pixel_val = int(img_array[py, px, 2])

            if bit == 1:
                pixel_val = pixel_val | 1  # Set LSB to 1
            else:
                pixel_val = pixel_val & 0xFE  # Set LSB to 0

            img_array[py, px, 2] = pixel_val

    logger.info(
        "Spatial layer embedded %d bits with %dx repetition (%d pixels used)",
        len(bits), REPS, PIXELS_NEEDED
    )
    return Image.fromarray(img_array)


def extract(image: Image.Image) -> tuple[bytes, float]:
    """
    Extract payload from LSBs of deterministically-selected pixels
    using majority vote per bit.

    Returns:
        (payload_bytes, confidence) where confidence represents
        the average vote agreement across all bits.
    """
    img_array = np.array(image.convert("RGB"), dtype=np.uint8)
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        logger.warning("Image too small for spatial extraction: %dx%d", w, h)
        return (b"", 0.0)

    try:
        positions = _get_pixel_positions(h, w, PIXELS_NEEDED)
    except ValueError:
        return (b"", 0.0)

    bits = []
    confidences = []

    for bit_idx in range(PAYLOAD_BITS):
        votes_for_1 = 0
        votes_for_0 = 0

        for rep in range(REPS):
            px_idx = bit_idx * REPS + rep
            py, px = positions[px_idx]

            pixel_val = int(img_array[py, px, 2])
            lsb = pixel_val & 1

            if lsb == 1:
                votes_for_1 += 1
            else:
                votes_for_0 += 1

        # Majority vote
        if votes_for_1 > votes_for_0:
            bits.append(1)
            conf = votes_for_1 / REPS
        else:
            bits.append(0)
            conf = votes_for_0 / REPS

        confidences.append(conf)

    payload = _bits_to_payload(bits)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    logger.info(
        "Spatial layer extracted %d bits, avg confidence=%.3f",
        len(bits), avg_confidence
    )
    return (payload, avg_confidence)
