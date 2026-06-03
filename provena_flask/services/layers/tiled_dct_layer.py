"""
Tiled DCT Layer — Holographic tiled frequency-domain watermarking for HydraWatermark.

Divides the image into overlapping 128×128 tiles and independently embeds
the FULL 48-bit payload into each tile using DCT Quantization Index Modulation
(QIM).  A Barker-13 synchronisation sequence is placed in the first row of
DCT coefficients of every tile so that extraction can reliably detect tile
boundaries even after arbitrary cropping.

Because tiles overlap by 50 % each pixel participates in up to four tiles.
During extraction a sliding window with stride 64 scans across the image,
extracts a candidate payload from every window, and the final result is
determined by majority vote across all windows.

Capacity : 48 bits per tile (identical payload replicated holographically).
Robustness: survives arbitrary crops — any 128×128 surviving region may
            be sufficient to recover the full payload.
"""
from __future__ import annotations

import logging
from collections import Counter

import numpy as np
from PIL import Image
from scipy.fftpack import dct, idct

logger = logging.getLogger(__name__)
LAYER_NAME = "tiled_dct"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TILE_SIZE = 128          # Tile width and height in pixels
OVERLAP = 0.5            # 50 % overlap between adjacent tiles
STRIDE = int(TILE_SIZE * (1 - OVERLAP))  # 64 px step

QUANT_STEP = 30          # QIM step — slightly stronger than the global DCT layer
COEFF_POS = (4, 3)       # Mid-frequency DCT coefficient position within 8×8 blocks

PAYLOAD_BYTES = 6
PAYLOAD_BITS = PAYLOAD_BYTES * 8  # 48

MIN_DIM = TILE_SIZE      # Image must be at least one tile wide/tall

# Barker-13 sync sequence (good auto-correlation properties)
BARKER_13 = [+1, +1, +1, +1, +1, -1, -1, +1, +1, -1, +1, -1, +1]
SYNC_LEN = len(BARKER_13)


# ---------------------------------------------------------------------------
# DCT helpers (reused from dct_layer.py maths)
# ---------------------------------------------------------------------------
def _dct2(block: np.ndarray) -> np.ndarray:
    """Compute 2-D DCT of an 8×8 block (ortho-normalised)."""
    return dct(dct(block.T, norm="ortho").T, norm="ortho")


def _idct2(block: np.ndarray) -> np.ndarray:
    """Compute 2-D inverse DCT of an 8×8 block (ortho-normalised)."""
    return idct(idct(block.T, norm="ortho").T, norm="ortho")


# ---------------------------------------------------------------------------
# Bit ↔ byte conversions
# ---------------------------------------------------------------------------
def _payload_to_bits(payload: bytes) -> list[int]:
    """Convert 6-byte payload to a list of 48 bits (MSB-first per byte)."""
    bits: list[int] = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_payload(bits: list[int]) -> bytes:
    """Convert a list of 48 bits back to 6 bytes."""
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte_bits = bits[i : i + 8]
        if len(byte_bits) < 8:
            byte_bits.extend([0] * (8 - len(byte_bits)))
        byte_val = 0
        for b in byte_bits:
            byte_val = (byte_val << 1) | (b & 1)
        result.append(byte_val)
    return bytes(result)


# ---------------------------------------------------------------------------
# Block-position selection within a single tile
# ---------------------------------------------------------------------------
def _get_block_positions_in_tile(num_bits: int) -> list[tuple[int, int]]:
    """Return 8×8-block (row, col) positions inside a TILE_SIZE×TILE_SIZE tile.

    The first row of blocks (row-index 0) is reserved for the Barker-13
    sync sequence, so payload blocks start from the second row onward.

    Positions are evenly spaced across the remaining blocks.
    """
    blocks_per_side = TILE_SIZE // 8  # 16
    # Reserve first block-row for sync
    payload_block_rows = blocks_per_side - 1  # 15
    payload_blocks = payload_block_rows * blocks_per_side  # 15 × 16 = 240

    if payload_blocks < num_bits:
        raise ValueError(
            f"Tile too small: {payload_blocks} payload blocks < {num_bits} bits"
        )

    step = payload_blocks / num_bits
    positions: list[tuple[int, int]] = []
    for i in range(num_bits):
        idx = int(i * step)
        br = 1 + idx // blocks_per_side   # +1 to skip sync row
        bc = idx % blocks_per_side
        positions.append((br * 8, bc * 8))
    return positions


# ---------------------------------------------------------------------------
# Embed / extract helpers for a single tile
# ---------------------------------------------------------------------------
def _embed_sync(tile: np.ndarray, channel: int = 0) -> None:
    """Embed Barker-13 sync sequence into the first row of 8×8 blocks."""
    for i, symbol in enumerate(BARKER_13):
        bc = i * 8
        if bc + 8 > tile.shape[1]:
            break
        block = tile[0:8, bc : bc + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        quantized = np.round(coeff / QUANT_STEP) * QUANT_STEP
        if symbol == 1:
            dct_block[COEFF_POS] = quantized + QUANT_STEP / 2
        else:
            dct_block[COEFF_POS] = quantized

        tile[0:8, bc : bc + 8, channel] = _idct2(dct_block)


def _detect_sync(tile: np.ndarray, channel: int = 0) -> float:
    """Return normalised correlation of extracted sync with Barker-13.

    A value close to 1.0 means the sync sequence is present.
    """
    extracted: list[int] = []
    for i in range(SYNC_LEN):
        bc = i * 8
        if bc + 8 > tile.shape[1]:
            return 0.0
        block = tile[0:8, bc : bc + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        remainder = coeff % QUANT_STEP
        half_step = QUANT_STEP / 2

        if abs(remainder - half_step) < abs(remainder) and abs(remainder - half_step) < abs(remainder - QUANT_STEP):
            extracted.append(+1)
        else:
            extracted.append(-1)

    # Normalised dot-product
    dot = sum(a * b for a, b in zip(BARKER_13, extracted))
    return dot / SYNC_LEN


def _embed_payload_in_tile(
    tile: np.ndarray,
    bits: list[int],
    channel: int = 0,
) -> None:
    """Embed payload bits into a single tile via DCT QIM."""
    positions = _get_block_positions_in_tile(len(bits))
    for bit, (by, bx) in zip(bits, positions):
        block = tile[by : by + 8, bx : bx + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        quantized = np.round(coeff / QUANT_STEP) * QUANT_STEP

        if bit == 1:
            dct_block[COEFF_POS] = quantized + QUANT_STEP / 2
        else:
            dct_block[COEFF_POS] = quantized

        tile[by : by + 8, bx : bx + 8, channel] = _idct2(dct_block)


def _extract_payload_from_tile(
    tile: np.ndarray,
    channel: int = 0,
) -> tuple[bytes, float]:
    """Extract payload bits from a single tile via DCT QIM.

    Returns (payload_bytes, avg_confidence).
    """
    positions = _get_block_positions_in_tile(PAYLOAD_BITS)
    bits: list[int] = []
    confidences: list[float] = []

    for by, bx in positions:
        block = tile[by : by + 8, bx : bx + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        remainder = coeff % QUANT_STEP
        half_step = QUANT_STEP / 2

        if abs(remainder - half_step) < abs(remainder) and abs(remainder - half_step) < abs(remainder - QUANT_STEP):
            bits.append(1)
            conf = 1.0 - abs(remainder - half_step) / half_step
        else:
            bits.append(0)
            min_dist = min(abs(remainder), abs(remainder - QUANT_STEP))
            conf = 1.0 - min_dist / half_step

        confidences.append(max(0.0, min(1.0, conf)))

    payload = _bits_to_payload(bits)
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return payload, avg_conf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """Embed *payload* holographically into overlapping 128×128 tiles.

    Args:
        image:   PIL Image (RGB).  Must be at least 128×128.
        payload: Exactly 6 bytes (48 bits).

    Returns:
        Watermarked PIL Image.

    Raises:
        ValueError: If *payload* length is wrong or image is too small.
    """
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError(
            f"Tiled DCT layer expects {PAYLOAD_BYTES} bytes, got {len(payload)}"
        )

    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        raise ValueError(
            f"Image too small for tiled DCT layer: {w}×{h}, "
            f"need at least {MIN_DIM}×{MIN_DIM}"
        )

    bits = _payload_to_bits(payload)
    tiles_embedded = 0

    # Walk across the image with TILE_SIZE (non-overlapping) during embedding to eliminate self-interference
    for ty in range(0, h - TILE_SIZE + 1, TILE_SIZE):
        for tx in range(0, w - TILE_SIZE + 1, TILE_SIZE):
            tile = img_array[ty : ty + TILE_SIZE, tx : tx + TILE_SIZE, :].copy()

            # 1. Embed sync sequence in the first block-row
            _embed_sync(tile)
            # 2. Embed payload in the remaining blocks
            _embed_payload_in_tile(tile, bits)

            # Write the modified tile back
            img_array[ty : ty + TILE_SIZE, tx : tx + TILE_SIZE, :] = tile
            tiles_embedded += 1

    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    logger.info(
        "Tiled DCT layer embedded %d bits into %d non-overlapping tiles "
        "(tile=%d)",
        len(bits),
        tiles_embedded,
        TILE_SIZE,
    )
    return Image.fromarray(img_array)


def _extract_with_channel(
    img_array: np.ndarray,
    h: int,
    w: int,
    channel: int,
) -> tuple[bytes, float]:
    """Helper to extract payload from a specific channel."""
    candidates: list[bytes] = []
    sync_threshold = 0.5

    for ty in range(0, h - TILE_SIZE + 1, STRIDE):
        for tx in range(0, w - TILE_SIZE + 1, STRIDE):
            tile = img_array[ty : ty + TILE_SIZE, tx : tx + TILE_SIZE, :].copy()

            # Check sync sequence
            sync_corr = _detect_sync(tile, channel=channel)
            if sync_corr < sync_threshold:
                continue  # Not a valid tile boundary

            payload, _conf = _extract_payload_from_tile(tile, channel=channel)
            candidates.append(payload)

    if not candidates:
        return (b"", 0.0)

    # Majority vote over candidate payloads
    counter = Counter(candidates)
    winner, winner_count = counter.most_common(1)[0]
    return winner, winner_count / len(candidates)


def extract(image: Image.Image) -> tuple[bytes, float]:
    """Extract payload by majority-voting across all sliding-window tiles.

    Supports dynamic fallback across Red (0), Green (1), and Blue (2) channels.
    """
    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_DIM or w < MIN_DIM:
        logger.warning(
            "Image too small for tiled DCT extraction: %d×%d", w, h
        )
        return (b"", 0.0)

    # Try primary channel (Red = 0) first
    winner, confidence = _extract_with_channel(img_array, h, w, channel=0)
    if confidence >= 0.5:
        logger.info("Tiled DCT: Extracted payload from Red channel (conf=%.3f)", confidence)
        return winner, confidence

    # Try fallback channel (Green = 1) for backward compatibility
    winner_g, confidence_g = _extract_with_channel(img_array, h, w, channel=1)
    if confidence_g >= 0.5:
        logger.info("Tiled DCT: Extracted payload from Green channel fallback (conf=%.3f)", confidence_g)
        return winner_g, confidence_g

    # Try fallback channel (Blue = 2) just in case
    winner_b, confidence_b = _extract_with_channel(img_array, h, w, channel=2)
    if confidence_b >= 0.5:
        logger.info("Tiled DCT: Extracted payload from Blue channel fallback (conf=%.3f)", confidence_b)
        return winner_b, confidence_b

    # Return best attempt if none are above threshold
    attempts = [(confidence, winner, 0), (confidence_g, winner_g, 1), (confidence_b, winner_b, 2)]
    best_conf, best_winner, best_ch = max(attempts, key=lambda x: x[0])
    logger.info("Tiled DCT: Best extraction attempt from channel %d (conf=%.3f)", best_ch, best_conf)
    return best_winner, best_conf
