"""
Tamper Grid — Tamper localisation service for PROVENA.

Divides an image into a grid of cells (each approximately 128 px) and embeds
a unique per-cell payload using DCT Quantization Index Modulation (QIM).
Each cell's payload encodes:

    ┌─────────────────────────────────────────────┐
    │  32 bits  record_id_int                     │
    │   4 bits  cell row   (0–15)                 │
    │   4 bits  cell col   (0–15)                 │
    │   8 bits  CRC-8 checksum                    │
    ├─────────────────────────────────────────────┤
    │  = 48 bits = 6 bytes per cell               │
    └─────────────────────────────────────────────┘

During extraction each cell is decoded independently.  If the recovered
record-id and position match the expected values **and** the CRC is valid
the cell is marked ``authentic``; otherwise ``tampered``.  Cells that cannot
be decoded at all are marked ``unknown``.

The result is a ``TamperReport`` dataclass containing per-cell status,
an overall authenticity percentage, and a list of tampered bounding boxes.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from scipy.fftpack import dct, idct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NOMINAL_CELL_SIZE = 128      # Target cell side-length in pixels
MIN_CELL_SIZE = 64           # Cells smaller than this cannot carry a payload
PAYLOAD_BYTES = 6            # 48-bit cell payload
PAYLOAD_BITS = PAYLOAD_BYTES * 8

QUANT_STEP = 35              # QIM step — distinct from dct_layer (30) to avoid interference
COEFF_POS = (5, 2)           # Mid-frequency DCT position — orthogonal to dct_layer (4,3)

MIN_CONFIDENCE = 0.35        # Below this a cell is classified as ``unknown``


# ---------------------------------------------------------------------------
# DCT helpers (same maths as dct_layer / tiled_dct_layer)
# ---------------------------------------------------------------------------
def _dct2(block: np.ndarray) -> np.ndarray:
    """Compute 2-D DCT of an 8×8 block (ortho-normalised)."""
    return dct(dct(block.T, norm="ortho").T, norm="ortho")


def _idct2(block: np.ndarray) -> np.ndarray:
    """Compute 2-D inverse DCT of an 8×8 block (ortho-normalised)."""
    return idct(idct(block.T, norm="ortho").T, norm="ortho")


# ---------------------------------------------------------------------------
# CRC-8 (polynomial 0x07, initial value 0x00)
# ---------------------------------------------------------------------------
_CRC8_TABLE: list[int] = []

def _init_crc8_table() -> None:
    """Pre-compute CRC-8 lookup table (polynomial 0x07)."""
    global _CRC8_TABLE  # noqa: PLW0603
    if _CRC8_TABLE:
        return
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        _CRC8_TABLE.append(crc)


def _crc8(data: bytes) -> int:
    """Compute CRC-8 for *data*."""
    _init_crc8_table()
    crc = 0x00
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc


# ---------------------------------------------------------------------------
# Cell payload encoding / decoding
# ---------------------------------------------------------------------------
def _encode_cell_payload(record_id_int: int, row: int, col: int) -> bytes:
    """Build a 6-byte cell payload.

    Layout (big-endian):
        bytes 0–3:  record_id_int  (uint32)
        byte  4:    (row << 4) | (col & 0x0F)
        byte  5:    CRC-8 of bytes 0–4
    """
    buf = struct.pack(">I", record_id_int & 0xFFFFFFFF)
    pos_byte = ((row & 0x0F) << 4) | (col & 0x0F)
    buf += bytes([pos_byte])
    buf += bytes([_crc8(buf)])
    return buf


def _decode_cell_payload(payload: bytes) -> tuple[int, int, int, bool]:
    """Decode a 6-byte cell payload.

    Returns:
        (record_id_int, row, col, crc_ok)
    """
    if len(payload) != PAYLOAD_BYTES or payload == b"\x00" * PAYLOAD_BYTES:
        return (0, 0, 0, False)

    expected_crc = _crc8(payload[:5])
    crc_ok = payload[5] == expected_crc

    record_id_int = struct.unpack(">I", payload[:4])[0]
    pos_byte = payload[4]
    row = (pos_byte >> 4) & 0x0F
    col = pos_byte & 0x0F

    return (record_id_int, row, col, crc_ok)


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
# Block positions inside a cell
# ---------------------------------------------------------------------------
def _get_block_positions_in_cell(
    cell_h: int, cell_w: int, num_bits: int
) -> list[tuple[int, int]]:
    """Return evenly-spaced 8×8-block positions inside a single cell."""
    blocks_y = cell_h // 8
    blocks_x = cell_w // 8
    total = blocks_y * blocks_x

    if total < num_bits:
        raise ValueError(
            f"Cell too small: {total} blocks < {num_bits} bits needed"
        )

    step = total / num_bits
    positions: list[tuple[int, int]] = []
    for i in range(num_bits):
        idx = int(i * step)
        br = idx // blocks_x
        bc = idx % blocks_x
        positions.append((br * 8, bc * 8))
    return positions


# ---------------------------------------------------------------------------
# Per-cell embed / extract
# ---------------------------------------------------------------------------
def _embed_cell(
    cell: np.ndarray,
    payload: bytes,
    channel: int = 2,
) -> None:
    """Embed *payload* into a cell region in-place using DCT QIM."""
    cell_h, cell_w = cell.shape[0], cell.shape[1]
    bits = _payload_to_bits(payload)
    positions = _get_block_positions_in_cell(cell_h, cell_w, len(bits))

    for bit, (by, bx) in zip(bits, positions):
        block = cell[by : by + 8, bx : bx + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        quantized = np.round(coeff / QUANT_STEP) * QUANT_STEP

        if bit == 1:
            dct_block[COEFF_POS] = quantized + QUANT_STEP / 2
        else:
            dct_block[COEFF_POS] = quantized

        cell[by : by + 8, bx : bx + 8, channel] = _idct2(dct_block)


def _extract_cell(
    cell: np.ndarray,
    channel: int = 2,
) -> tuple[bytes, float]:
    """Extract payload from a single cell.

    Returns:
        (payload_bytes, avg_confidence)
    """
    cell_h, cell_w = cell.shape[0], cell.shape[1]

    try:
        positions = _get_block_positions_in_cell(cell_h, cell_w, PAYLOAD_BITS)
    except ValueError:
        return (b"", 0.0)

    bits: list[int] = []
    confidences: list[float] = []

    for by, bx in positions:
        block = cell[by : by + 8, bx : bx + 8, channel].copy()
        dct_block = _dct2(block)

        coeff = dct_block[COEFF_POS]
        remainder = coeff % QUANT_STEP
        half_step = QUANT_STEP / 2

        if abs(remainder - half_step) < abs(remainder) and abs(
            remainder - half_step
        ) < abs(remainder - QUANT_STEP):
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
# Dataclasses for the tamper report
# ---------------------------------------------------------------------------
@dataclass
class CellResult:
    """Result of tamper analysis for a single grid cell."""

    row: int
    col: int
    status: str              # 'authentic' | 'tampered' | 'unknown'
    confidence: float
    bbox: tuple[int, int, int, int]   # (x0, y0, x1, y1) in pixel coordinates


@dataclass
class TamperReport:
    """Aggregate tamper-analysis report for the full image."""

    grid_rows: int
    grid_cols: int
    cells: list[CellResult] = field(default_factory=list)
    authentic_percentage: float = 0.0
    tampered_regions: list[tuple[int, int, int, int]] = field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Grid geometry
# ---------------------------------------------------------------------------
def _compute_grid(
    img_h: int, img_w: int
) -> tuple[int, int, int, int]:
    """Compute adaptive grid dimensions.

    Returns:
        (grid_rows, grid_cols, cell_h, cell_w)
    """
    grid_rows = max(1, round(img_h / NOMINAL_CELL_SIZE))
    grid_cols = max(1, round(img_w / NOMINAL_CELL_SIZE))

    cell_h = img_h // grid_rows
    cell_w = img_w // grid_cols

    # Ensure cells are large enough to carry a payload
    while cell_h < MIN_CELL_SIZE and grid_rows > 1:
        grid_rows -= 1
        cell_h = img_h // grid_rows

    while cell_w < MIN_CELL_SIZE and grid_cols > 1:
        grid_cols -= 1
        cell_w = img_w // grid_cols

    return grid_rows, grid_cols, cell_h, cell_w


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def embed(
    image: Image.Image,
    record_id_int: int,
    payload: bytes,  # kept for API symmetry; overridden per-cell
) -> Image.Image:
    """Embed per-cell tamper payloads into the image.

    Each cell receives a unique payload containing the *record_id_int*,
    its (row, col) position, and a CRC-8 checksum.

    Args:
        image:         PIL Image (RGB).  Min 128×128.
        record_id_int: 32-bit record identifier.
        payload:       Ignored internally (each cell builds its own).
                       Kept for API symmetry with other layers.

    Returns:
        Watermarked PIL Image.

    Raises:
        ValueError: If the image is too small.
    """
    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_CELL_SIZE or w < MIN_CELL_SIZE:
        raise ValueError(
            f"Image too small for tamper grid: {w}×{h}, "
            f"need at least {MIN_CELL_SIZE}×{MIN_CELL_SIZE}"
        )

    grid_rows, grid_cols, cell_h, cell_w = _compute_grid(h, w)

    # Check row/col fit in 4 bits
    if grid_rows > 16 or grid_cols > 16:
        logger.warning(
            "Grid exceeds 16×16 — clamping to 16.  "
            "Some cells will share position nibbles."
        )
        grid_rows = min(grid_rows, 16)
        grid_cols = min(grid_cols, 16)
        cell_h = h // grid_rows
        cell_w = w // grid_cols

    cells_embedded = 0

    for r in range(grid_rows):
        for c in range(grid_cols):
            y0 = r * cell_h
            x0 = c * cell_w
            # Last row/col absorbs remainder pixels
            y1 = (r + 1) * cell_h if r < grid_rows - 1 else h
            x1 = (c + 1) * cell_w if c < grid_cols - 1 else w

            ch = y1 - y0
            cw = x1 - x0

            # Skip cells that are too small to carry a payload
            if ch < MIN_CELL_SIZE or cw < MIN_CELL_SIZE:
                logger.debug(
                    "Skipping cell (%d,%d): size %d×%d too small", r, c, cw, ch
                )
                continue

            cell_payload = _encode_cell_payload(record_id_int, r, c)
            cell = img_array[y0:y1, x0:x1, :].copy()

            # Trim cell to nearest 8-px boundary for DCT
            usable_h = (ch // 8) * 8
            usable_w = (cw // 8) * 8
            if usable_h < MIN_CELL_SIZE or usable_w < MIN_CELL_SIZE:
                continue

            _embed_cell(cell[:usable_h, :usable_w, :], cell_payload)
            img_array[y0 : y0 + usable_h, x0 : x0 + usable_w, :] = cell[
                :usable_h, :usable_w, :
            ]
            cells_embedded += 1

    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    logger.info(
        "Tamper grid embedded %d cells (%d×%d grid, cell≈%d×%d px)",
        cells_embedded,
        grid_rows,
        grid_cols,
        cell_w,
        cell_h,
    )
    return Image.fromarray(img_array)


def _extract_with_channel(
    img_array: np.ndarray,
    h: int,
    w: int,
    grid_rows: int,
    grid_cols: int,
    cell_h: int,
    cell_w: int,
    channel: int,
) -> TamperReport:
    """Helper to extract and verify cells from a specific channel."""
    report = TamperReport(grid_rows=grid_rows, grid_cols=grid_cols)
    authentic_count = 0
    tampered_count = 0

    for r in range(grid_rows):
        for c in range(grid_cols):
            y0 = r * cell_h
            x0 = c * cell_w
            y1 = (r + 1) * cell_h if r < grid_rows - 1 else h
            x1 = (c + 1) * cell_w if c < grid_cols - 1 else w

            ch = y1 - y0
            cw = x1 - x0
            bbox = (x0, y0, x1, y1)

            # Skip cells that are too small
            usable_h = (ch // 8) * 8
            usable_w = (cw // 8) * 8
            if usable_h < MIN_CELL_SIZE or usable_w < MIN_CELL_SIZE:
                report.cells.append(
                    CellResult(
                        row=r,
                        col=c,
                        status="unknown",
                        confidence=0.0,
                        bbox=bbox,
                    )
                )
                continue

            cell = img_array[y0 : y0 + usable_h, x0 : x0 + usable_w, :].copy()
            payload, confidence = _extract_cell(cell, channel=channel)

            if confidence < MIN_CONFIDENCE or len(payload) != PAYLOAD_BYTES:
                report.cells.append(
                    CellResult(
                        row=r,
                        col=c,
                        status="unknown",
                        confidence=confidence,
                        bbox=bbox,
                    )
                )
                continue

            record_id_int, dec_row, dec_col, crc_ok = _decode_cell_payload(
                payload
            )

            if crc_ok and dec_row == r and dec_col == c:
                status = "authentic"
                authentic_count += 1
            else:
                status = "tampered"
                tampered_count += 1
                report.tampered_regions.append(bbox)

            report.cells.append(
                CellResult(
                    row=r,
                    col=c,
                    status=status,
                    confidence=confidence,
                    bbox=bbox,
                )
            )

    total_evaluated = authentic_count + tampered_count
    report.authentic_percentage = (
        (authentic_count / total_evaluated * 100.0) if total_evaluated > 0 else 0.0
    )
    return report


def extract(image: Image.Image) -> TamperReport:
    """Extract and verify per-cell payloads to produce a tamper report.

    Supports dynamic channel fallback across Blue (2), Green (1), and Red (0) channels.
    """
    img_array = np.array(image.convert("RGB"), dtype=np.float64)
    h, w, _ = img_array.shape

    if h < MIN_CELL_SIZE or w < MIN_CELL_SIZE:
        logger.warning("Image too small for tamper grid extraction: %d×%d", w, h)
        return TamperReport(grid_rows=0, grid_cols=0)

    grid_rows, grid_cols, cell_h, cell_w = _compute_grid(h, w)

    # Clamp to 16 as during embedding
    grid_rows = min(grid_rows, 16)
    grid_cols = min(grid_cols, 16)
    cell_h = h // grid_rows
    cell_w = w // grid_cols

    # Try Blue channel (2) first (new design)
    report_b = _extract_with_channel(img_array, h, w, grid_rows, grid_cols, cell_h, cell_w, channel=2)
    if report_b.authentic_percentage >= 50.0:
        logger.info("TamperGrid: Using Blue channel extraction (%.1f%% authentic)", report_b.authentic_percentage)
        return report_b

    # Fallback to Green channel (1) (legacy design / compatibility)
    report_g = _extract_with_channel(img_array, h, w, grid_rows, grid_cols, cell_h, cell_w, channel=1)
    if report_g.authentic_percentage >= 50.0 or report_g.authentic_percentage > report_b.authentic_percentage:
        logger.info("TamperGrid: Using Green channel fallback (%.1f%% authentic)", report_g.authentic_percentage)
        return report_g

    # Try Red channel (0) just in case
    report_r = _extract_with_channel(img_array, h, w, grid_rows, grid_cols, cell_h, cell_w, channel=0)
    if report_r.authentic_percentage >= 50.0 or report_r.authentic_percentage > report_b.authentic_percentage:
        logger.info("TamperGrid: Using Red channel fallback (%.1f%% authentic)", report_r.authentic_percentage)
        return report_r

    logger.info("TamperGrid: Best extraction from Blue channel (%.1f%% authentic)", report_b.authentic_percentage)
    return report_b
