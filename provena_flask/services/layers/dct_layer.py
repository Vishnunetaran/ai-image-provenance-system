"""
DCT Layer — Frequency-domain watermarking for HydraWatermark.

Embeds payload bits into mid-frequency DCT coefficients of 8x8 blocks
using Quantization Index Modulation (QIM). This layer is robust against
spatial-domain attacks (neural stripping, spatial filtering) but weaker
against heavy JPEG compression (which also operates in DCT domain).

Capacity: 48 bits embedded across selected 8x8 blocks in the image.
"""
from __future__ import annotations

import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)
LAYER_NAME = "dct_frequency"

# QIM quantization step — controls strength vs. invisibility tradeoff
# Higher = more robust but more visible. 25 is a good balance.
QUANT_STEP = 25

# Which DCT coefficient position within each 8x8 block to use for embedding.
# Position (4,3) is mid-frequency — survives mild compression, invisible to eyes.
COEFF_POS = (4, 3)

# Minimum image dimension to operate on
MIN_DIM = 64

# Payload size
PAYLOAD_BYTES = 6


def _dct2(block: np.ndarray) -> np.ndarray:
    """Compute 2D DCT of an 8x8 block using the naive matrix approach."""
    from scipy.fftpack import dct
    return dct(dct(block.T, norm='ortho').T, norm='ortho')


def _idct2(block: np.ndarray) -> np.ndarray:
    """Compute 2D inverse DCT of an 8x8 block."""
    from scipy.fftpack import idct
    return idct(idct(block.T, norm='ortho').T, norm='ortho')


def _payload_to_bits(payload: bytes) -> list[int]:
    """Convert 6-byte payload to list of 48 bits."""
    bits = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_payload(bits: list[int]) -> bytes:
    """Convert list of 48 bits back to 6 bytes."""
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


def _get_block_positions(h: int, w: int, num_bits: int) -> list[tuple[int, int]]:
    """Select which 8x8 blocks to use for embedding, spread evenly across the image."""
    blocks_y = h // 8
    blocks_x = w // 8
    total_blocks = blocks_y * blocks_x

    if total_blocks < num_bits:
        raise ValueError(f"Image too small: {total_blocks} blocks < {num_bits} bits needed")

    # Evenly space blocks across the image for maximum robustness
    step = total_blocks / num_bits
    positions = []
    for i in range(num_bits):
        idx = int(i * step)
        by = idx // blocks_x
        bx = idx % blocks_x
        positions.append((by * 8, bx * 8))
    return positions


def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed payload into DCT coefficients of the image.

    Args:
        image:   PIL Image (RGB). Min 64x64.
        payload: Exactly 6 bytes.

    Returns:
        Watermarked PIL Image.
    """
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError(f"DCT layer expects {PAYLOAD_BYTES} bytes, got {len(payload)}")

    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        raise ValueError(f"Image too small for DCT layer: {w}x{h}, need {MIN_DIM}x{MIN_DIM}")

    bits = _payload_to_bits(payload)
    positions = _get_block_positions(h, w, len(bits))

    # Work on the luminance-like channel (green channel has highest perceptual weight)
    channel = 1  # Green channel

    for bit, (by, bx) in zip(bits, positions):
        block = img_array[by:by + 8, bx:bx + 8, channel].copy()
        dct_block = _dct2(block)

        # QIM embedding: quantize the coefficient to encode 0 or 1
        coeff = dct_block[COEFF_POS]
        quantized = np.round(coeff / QUANT_STEP) * QUANT_STEP

        if bit == 1:
            # Force coefficient to nearest odd multiple of QUANT_STEP/2
            dct_block[COEFF_POS] = quantized + QUANT_STEP / 2
        else:
            # Force coefficient to nearest even multiple of QUANT_STEP/2
            dct_block[COEFF_POS] = quantized

        img_array[by:by + 8, bx:bx + 8, channel] = _idct2(dct_block)

    # Clip to valid range
    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    logger.info("DCT layer embedded %d bits across %d blocks", len(bits), len(positions))
    return Image.fromarray(img_array)


def extract(image: Image.Image) -> tuple[bytes, float]:
    """
    Extract payload from DCT coefficients.

    Returns:
        (payload_bytes, confidence) where confidence is the fraction of
        bits that decoded with strong signal.
    """
    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        logger.warning("Image too small for DCT extraction: %dx%d", w, h)
        return (b"", 0.0)

    num_bits = PAYLOAD_BYTES * 8  # 48
    try:
        positions = _get_block_positions(h, w, num_bits)
    except ValueError:
        return (b"", 0.0)

    channel = 1  # Green channel
    bits = []
    confidences = []

    for by, bx in positions:
        block = img_array[by:by + 8, bx:bx + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        # QIM decoding: check if coefficient is closer to odd or even quantization
        remainder = coeff % QUANT_STEP
        half_step = QUANT_STEP / 2

        if abs(remainder - half_step) < abs(remainder) and abs(remainder - half_step) < abs(remainder - QUANT_STEP):
            bits.append(1)
            # Confidence = how close to the ideal '1' position
            conf = 1.0 - abs(remainder - half_step) / (QUANT_STEP / 2)
        else:
            bits.append(0)
            min_dist = min(abs(remainder), abs(remainder - QUANT_STEP))
            conf = 1.0 - min_dist / (QUANT_STEP / 2)

        confidences.append(max(0.0, min(1.0, conf)))

    payload = _bits_to_payload(bits)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    logger.info("DCT layer extracted %d bits, avg confidence=%.3f", len(bits), avg_confidence)
    return (payload, avg_confidence)
