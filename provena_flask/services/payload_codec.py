"""
Payload Codec — T-010, T-011
48-bit compact record-ID encoding for neural watermark payloads.

Layout (48 bits / 6 bytes total):
  Data section  (4 bytes / 32 bits):
    record_id : 4 bytes (big-endian unsigned int, max ~4 billion records)

  ECC section   (2 bytes / 16 bits):
    Reed-Solomon parity (1 ECC symbol = corrects 1 byte = 8 bits of error)

# TODO: expand to 256-bit payload when GPU is available
"""
from __future__ import annotations

import logging
import struct

import reedsolo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_BYTES  = 4        # 32 bits of record ID
ECC_BYTES   = 2        # 16 bits of Reed-Solomon parity (nsym=1 → corrects 1 symbol)
TOTAL_BYTES = DATA_BYTES + ECC_BYTES   # 6 bytes / 48 bits total

# RSCodec(1) → 1 ECC symbol → corrects up to 1 byte (8 bits) of error
_RS = reedsolo.RSCodec(ECC_BYTES)


class PayloadDecodeError(Exception):
    """Raised when ECC correction fails or structure is invalid."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode(record_id: int) -> bytes:
    """
    Encode a record_id into a 6-byte RS-ECC protected codeword.

    Args:
        record_id: Non-negative integer (0 to 2^32 - 1).

    Returns:
        Exactly 6 bytes (4 data + 2 ECC parity).

    Raises:
        ValueError: If record_id is out of range.
    """
    if not (0 <= record_id <= 0xFFFFFFFF):
        raise ValueError(f"record_id must be 0–4294967295, got {record_id}")

    data = struct.pack(">I", record_id)  # 4 bytes, big-endian unsigned int
    assert len(data) == DATA_BYTES

    encoded = bytes(_RS.encode(bytearray(data)))
    logger.debug("Encoded payload: record_id=%d -> %s", record_id, encoded.hex())
    return encoded


def decode(codeword: bytes) -> int:
    """
    Decode a 6-byte codeword, applying ECC correction.

    Args:
        codeword: 6-byte bytes object (output from encode(), possibly with errors).

    Returns:
        The recovered record_id as an integer.

    Raises:
        PayloadDecodeError: If ECC correction fails or length is wrong.
    """
    if len(codeword) != TOTAL_BYTES:
        raise PayloadDecodeError(
            f"Expected {TOTAL_BYTES} bytes, got {len(codeword)}"
        )

    if all(b == 0 for b in codeword):
        raise PayloadDecodeError("Codeword is entirely empty/zeros")

    try:
        corrected, _, _ = _RS.decode(bytearray(codeword))
        data = bytes(corrected)
    except reedsolo.ReedSolomonError as exc:
        raise PayloadDecodeError(f"ECC correction failed: {exc}") from exc

    if len(data) != DATA_BYTES:
        raise PayloadDecodeError(
            f"Corrected data length {len(data)} != expected {DATA_BYTES}"
        )

    record_id = struct.unpack(">I", data)[0]
    logger.debug("Decoded payload: record_id=%d", record_id)
    return record_id
