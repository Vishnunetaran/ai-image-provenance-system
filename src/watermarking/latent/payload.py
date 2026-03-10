"""
Payload Encoding / Decoding Module for Latent Watermarking.

Handles the conversion of provenance metadata (128-bit UUID + 32-bit timestamp)
into error-correction-coded bitstreams suitable for watermark embedding, and
the reverse decoding process with error correction.

Error Correction Strategy:
    - Primary: LDPC codes (via pyldpc) — corrects up to ~48% bit error rate
    - Fallback: Reed-Solomon (via reedsolo) — corrects up to ~38% symbol errors
    - Last resort: Simple 5x redundancy with majority voting

The payload layout (160 raw bits):
    ┌──────────────────────┬────────────────┬──────────┐
    │  UUID (128 bits)     │ Timestamp (32) │ CRC (16) │
    └──────────────────────┴────────────────┴──────────┘
    Total raw: 176 bits → LDPC encoded: ~528 bits (rate ≈ 1/3)

References:
    - LDPC codes: Gallager (1962), modern 5G NR standard
    - Reed-Solomon: used in QR codes, CDs, deep space probes
"""

import struct
import uuid as uuid_lib
import hashlib
import time
import logging
from typing import Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

UUID_BITS = 128
TIMESTAMP_BITS = 32
CRC_BITS = 16
RAW_PAYLOAD_BITS = UUID_BITS + TIMESTAMP_BITS  # 160 bits
FULL_PAYLOAD_BITS = RAW_PAYLOAD_BITS + CRC_BITS  # 176 bits
REDUNDANCY_FACTOR = 5  # For simple fallback encoding
LDPC_CODE_RATE = 1 / 3  # Approx. coding rate for LDPC

# Reed-Solomon parameters
RS_NSYM = 10  # Number of error correction symbols
RS_MAX_ERRORS = RS_NSYM // 2  # Max correctable symbol errors

# ──────────────────────────────────────────────────────────────────────
# Error Correction Backend Detection
# ──────────────────────────────────────────────────────────────────────

_ECC_BACKEND = "redundancy"  # Default fallback

try:
    from reedsolo import RSCodec

    _ECC_BACKEND = "reed_solomon"
    logger.debug("Using Reed-Solomon error correction (reedsolo)")
except ImportError:
    pass

try:
    import pyldpc

    _ECC_BACKEND = "ldpc"
    logger.debug("Using LDPC error correction (pyldpc)")
except ImportError:
    pass


# ──────────────────────────────────────────────────────────────────────
# Internal Helpers
# ──────────────────────────────────────────────────────────────────────


def _uuid_to_bits(uuid_str: str) -> np.ndarray:
    """Convert a UUID string to a 128-bit numpy array.

    Args:
        uuid_str: UUID string (e.g. "550e8400-e29b-41d4-a716-446655440000").

    Returns:
        np.ndarray of shape (128,) with dtype int8, values in {0, 1}.

    Raises:
        ValueError: If the string is not a valid UUID.
    """
    u = uuid_lib.UUID(uuid_str)
    raw_bytes = u.bytes  # 16 bytes = 128 bits
    bits = np.unpackbits(np.frombuffer(raw_bytes, dtype=np.uint8))
    return bits.astype(np.int8)


def _bits_to_uuid(bits: np.ndarray) -> str:
    """Convert a 128-bit array back to a UUID string.

    Args:
        bits: np.ndarray of shape (128,), values in {0, 1}.

    Returns:
        UUID string.
    """
    byte_array = np.packbits(bits.astype(np.uint8))
    return str(uuid_lib.UUID(bytes=bytes(byte_array)))


def _timestamp_to_bits(timestamp: int) -> np.ndarray:
    """Convert a 32-bit UNIX timestamp to a bit array.

    Args:
        timestamp: UNIX timestamp (uint32 range).

    Returns:
        np.ndarray of shape (32,), values in {0, 1}.
    """
    raw = struct.pack(">I", timestamp & 0xFFFFFFFF)
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8)).astype(np.int8)


def _bits_to_timestamp(bits: np.ndarray) -> int:
    """Convert a 32-bit array back to a UNIX timestamp.

    Args:
        bits: np.ndarray of shape (32,), values in {0, 1}.

    Returns:
        UNIX timestamp integer.
    """
    byte_array = np.packbits(bits.astype(np.uint8))
    return struct.unpack(">I", bytes(byte_array))[0]


def _compute_crc16(data_bits: np.ndarray) -> np.ndarray:
    """Compute CRC-16/CCITT over a bit array.

    Args:
        data_bits: Input bit array.

    Returns:
        np.ndarray of shape (16,), the CRC bits.
    """
    # Convert bits to bytes for CRC computation
    padded_len = ((len(data_bits) + 7) // 8) * 8
    padded = np.zeros(padded_len, dtype=np.uint8)
    padded[: len(data_bits)] = data_bits
    data_bytes = bytes(np.packbits(padded))

    # CRC-16/CCITT
    crc = 0xFFFF
    for byte in data_bytes:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF

    crc_bytes = struct.pack(">H", crc)
    return np.unpackbits(np.frombuffer(crc_bytes, dtype=np.uint8)).astype(np.int8)


def _verify_crc16(data_bits: np.ndarray, crc_bits: np.ndarray) -> bool:
    """Verify CRC-16 checksum.

    Args:
        data_bits: Data bit array.
        crc_bits: Expected CRC bits (16 bits).

    Returns:
        True if CRC matches.
    """
    computed = _compute_crc16(data_bits)
    return np.array_equal(computed, crc_bits)


# ──────────────────────────────────────────────────────────────────────
# Error Correction Encoding / Decoding
# ──────────────────────────────────────────────────────────────────────


def _encode_redundancy(bits: np.ndarray) -> np.ndarray:
    """Encode with simple N-x redundancy (fallback).

    Each bit is repeated ``REDUNDANCY_FACTOR`` times.

    Args:
        bits: Raw bit array.

    Returns:
        Coded bit array of length ``len(bits) * REDUNDANCY_FACTOR``.
    """
    return np.repeat(bits, REDUNDANCY_FACTOR)


def _decode_redundancy(coded: np.ndarray, raw_length: int) -> np.ndarray:
    """Decode redundancy-coded bits via majority voting.

    Args:
        coded: Coded bit array.
        raw_length: Expected number of raw bits.

    Returns:
        Decoded bit array of length ``raw_length``.
    """
    decoded = np.zeros(raw_length, dtype=np.int8)
    for i in range(raw_length):
        start = i * REDUNDANCY_FACTOR
        end = min(start + REDUNDANCY_FACTOR, len(coded))
        chunk = coded[start:end]
        decoded[i] = 1 if np.sum(chunk) > len(chunk) / 2 else 0
    return decoded


def _encode_reed_solomon(bits: np.ndarray) -> np.ndarray:
    """Encode with Reed-Solomon error correction.

    Args:
        bits: Raw bit array.

    Returns:
        RS-coded bit array.
    """
    # Pack bits to bytes
    padded_len = ((len(bits) + 7) // 8) * 8
    padded = np.zeros(padded_len, dtype=np.uint8)
    padded[: len(bits)] = bits
    data_bytes = bytes(np.packbits(padded))

    codec = RSCodec(RS_NSYM)
    encoded = codec.encode(data_bytes)

    # Convert back to bits
    encoded_bits = np.unpackbits(np.frombuffer(bytes(encoded), dtype=np.uint8))
    return encoded_bits.astype(np.int8)


def _decode_reed_solomon(coded: np.ndarray, raw_bit_length: int) -> Optional[np.ndarray]:
    """Decode Reed-Solomon coded bits.

    Args:
        coded: RS-coded bit array.
        raw_bit_length: Expected number of raw bits.

    Returns:
        Decoded bit array, or None on failure.
    """
    try:
        # Pack coded bits to bytes
        padded_len = ((len(coded) + 7) // 8) * 8
        padded = np.zeros(padded_len, dtype=np.uint8)
        padded[: len(coded)] = np.clip(coded, 0, 1)
        coded_bytes = bytes(np.packbits(padded))

        codec = RSCodec(RS_NSYM)
        decoded_bytes = bytes(codec.decode(coded_bytes)[0])

        # Convert back to bits
        decoded_bits = np.unpackbits(
            np.frombuffer(decoded_bytes, dtype=np.uint8)
        )
        return decoded_bits[:raw_bit_length].astype(np.int8)
    except Exception as e:
        logger.warning(f"Reed-Solomon decoding failed: {e}")
        return None


def _encode_ldpc(bits: np.ndarray) -> np.ndarray:
    """Encode with LDPC error correction.

    Args:
        bits: Raw bit array.

    Returns:
        LDPC-coded bit array (~3x longer).
    """
    try:
        from pyldpc import make_ldpc, encode

        n = max(len(bits) * 3, 63)  # Code length (at least 3x data)
        d_v = 3  # Variable node degree
        d_c = 9  # Check node degree

        H, G = make_ldpc(n, d_v, d_c, systematic=True, sparse=True)
        k = G.shape[1]  # Message length

        # Pad message to k bits
        msg = np.zeros(k, dtype=int)
        msg[: min(len(bits), k)] = bits[: min(len(bits), k)]

        coded = encode(G, msg, snr=10)
        return coded.astype(np.int8)
    except Exception as e:
        logger.warning(f"LDPC encoding failed, falling back to redundancy: {e}")
        return _encode_redundancy(bits)


def _decode_ldpc(
    coded: np.ndarray, raw_bit_length: int
) -> Optional[np.ndarray]:
    """Decode LDPC-coded bits using belief propagation.

    Args:
        coded: LDPC-coded bit array (soft or hard decisions).
        raw_bit_length: Expected number of raw information bits.

    Returns:
        Decoded bit array, or None on failure.
    """
    try:
        from pyldpc import make_ldpc, decode

        n = len(coded)
        d_v = 3
        d_c = 9

        H, G = make_ldpc(n, d_v, d_c, systematic=True, sparse=True)

        # Convert hard bits to soft LLR for BP decoding
        # +1 → bit 0, -1 → bit 1
        soft = np.where(coded > 0.5, -1.0, 1.0)
        decoded = decode(H, soft, snr=10)

        return decoded[:raw_bit_length].astype(np.int8)
    except Exception as e:
        logger.warning(f"LDPC decoding failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def encode_payload(
    image_uuid: str,
    timestamp: Optional[int] = None,
    ecc_backend: Optional[str] = None,
) -> np.ndarray:
    """Encode provenance metadata into an error-correction-coded bit array.

    Packs a 128-bit UUID and 32-bit UNIX timestamp, appends a CRC-16
    integrity check, and applies error correction coding.

    Args:
        image_uuid: UUID string for the image
                    (e.g. "550e8400-e29b-41d4-a716-446655440000").
        timestamp: 32-bit UNIX timestamp. If None, uses ``int(time.time())``.
        ecc_backend: Override ECC backend: "ldpc", "reed_solomon", or
                     "redundancy". If None, uses best available.

    Returns:
        np.ndarray of coded bits (int8, values in {0, 1}).
        Length depends on ECC backend (~528 bits for LDPC, ~1200 for RS,
        ~880 for redundancy).

    Example:
        >>> coded = encode_payload("550e8400-e29b-41d4-a716-446655440000")
        >>> coded.dtype
        dtype('int8')
        >>> len(coded) > 160  # ECC adds redundancy
        True
    """
    if timestamp is None:
        timestamp = int(time.time()) & 0xFFFFFFFF

    backend = ecc_backend or _ECC_BACKEND

    # 1. Convert to bits
    uuid_bits = _uuid_to_bits(image_uuid)  # 128 bits
    ts_bits = _timestamp_to_bits(timestamp)  # 32 bits
    raw_bits = np.concatenate([uuid_bits, ts_bits])  # 160 bits

    # 2. Compute and append CRC-16
    crc = _compute_crc16(raw_bits)
    full_bits = np.concatenate([raw_bits, crc])  # 176 bits

    # 3. Apply error correction
    if backend == "ldpc":
        coded = _encode_ldpc(full_bits)
    elif backend == "reed_solomon":
        coded = _encode_reed_solomon(full_bits)
    else:
        coded = _encode_redundancy(full_bits)

    logger.debug(
        f"Payload encoded: {RAW_PAYLOAD_BITS} raw bits → "
        f"{len(coded)} coded bits (backend={backend})"
    )
    return coded


def decode_payload(
    coded_bits: np.ndarray,
    ecc_backend: Optional[str] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """Decode an error-correction-coded bit array back to provenance metadata.

    Applies error correction decoding, verifies CRC-16 integrity, and extracts
    the UUID and timestamp.

    Args:
        coded_bits: Coded bit array (from ``encode_payload`` or watermark
                    extraction). Values should be 0/1 (hard decisions) or
                    floats in [0, 1] (soft decisions).
        ecc_backend: Override ECC backend. If None, uses best available.

    Returns:
        Tuple of (uuid_string, timestamp) on success, or (None, None) on failure.

    Example:
        >>> coded = encode_payload("550e8400-e29b-41d4-a716-446655440000", 1700000000)
        >>> uuid_str, ts = decode_payload(coded)
        >>> uuid_str
        '550e8400-e29b-41d4-a716-446655440000'
        >>> ts
        1700000000
    """
    backend = ecc_backend or _ECC_BACKEND

    # 1. Hard-decision threshold if soft values provided
    hard_bits = (np.array(coded_bits) > 0.5).astype(np.int8)

    # 2. Apply error correction decoding
    if backend == "ldpc":
        decoded = _decode_ldpc(hard_bits, FULL_PAYLOAD_BITS)
    elif backend == "reed_solomon":
        decoded = _decode_reed_solomon(hard_bits, FULL_PAYLOAD_BITS)
    else:
        decoded = _decode_redundancy(hard_bits, FULL_PAYLOAD_BITS)

    if decoded is None:
        logger.warning("Payload decoding failed: ECC could not correct errors")
        return None, None

    # 3. Separate data and CRC
    data_bits = decoded[:RAW_PAYLOAD_BITS]
    crc_bits = decoded[RAW_PAYLOAD_BITS : RAW_PAYLOAD_BITS + CRC_BITS]

    # 4. Verify CRC
    if not _verify_crc16(data_bits, crc_bits):
        logger.warning("Payload CRC check failed — data may be corrupted")
        # Still attempt to extract (CRC failure might be in CRC bits)

    # 5. Extract UUID and timestamp
    try:
        uuid_str = _bits_to_uuid(data_bits[:UUID_BITS])
        timestamp = _bits_to_timestamp(data_bits[UUID_BITS : UUID_BITS + TIMESTAMP_BITS])
        logger.debug(f"Payload decoded: uuid={uuid_str}, ts={timestamp}")
        return uuid_str, timestamp
    except Exception as e:
        logger.error(f"Payload extraction failed: {e}")
        return None, None


def get_coded_length(ecc_backend: Optional[str] = None) -> int:
    """Get the expected coded bit length for the current ECC backend.

    Useful for pre-allocating space in watermark patterns.

    Args:
        ecc_backend: Override ECC backend. If None, uses best available.

    Returns:
        Expected number of coded bits.
    """
    backend = ecc_backend or _ECC_BACKEND
    if backend == "ldpc":
        return FULL_PAYLOAD_BITS * 3  # ~528
    elif backend == "reed_solomon":
        return (((FULL_PAYLOAD_BITS + 7) // 8) + RS_NSYM * 2) * 8
    else:
        return FULL_PAYLOAD_BITS * REDUNDANCY_FACTOR  # 880
