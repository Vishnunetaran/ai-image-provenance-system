"""
Payload Codec — T-010, T-011
256-bit codeword encoding/decoding for neural watermark payloads.

Layout (256 bits total):
  Data section  (20 bytes / 160 bits):
    model_id_idx   : 1 byte  (8 bits)   -> index into registered model list (0-255)
    timestamp_epoch: 5 bytes (40 bits)  -> Unix epoch seconds (valid until ~2109)
    session_hmac   : 8 bytes (64 bits)  -> truncated HMAC(user_id+nonce)
    phash_prefix   : 6 bytes (48 bits)  -> first 48 bits of original image pHash

  ECC section    (12 bytes / 96 bits):
    Reed-Solomon parity across the 20 data bytes.
    Corrects up to 6 symbol errors (each symbol = 1 byte), i.e., ~30 burst bit errors.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import struct
from dataclasses import dataclass

import reedsolo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_BYTES = 20       # 160 bits of actual provenance data
ECC_BYTES  = 12       # 96 bits of Reed-Solomon parity
TOTAL_BYTES = DATA_BYTES + ECC_BYTES   # 256 bits total

# reedsolo uses GF(2^8) with symbol size = 1 byte.
# nsym=12 corrects up to floor(12/2)=6 symbol errors, which covers ~30 burst bit errors.
_RS = reedsolo.RSCodec(ECC_BYTES)


class PayloadDecodeError(Exception):
    """Raised when ECC correction fails (too many errors) or structure is invalid."""


@dataclass
class DecodedPayload:
    """Decoded provenance payload fields."""
    model_id_idx: int           # 0-255 model index
    timestamp: int              # Unix epoch seconds
    session_token: bytes        # 8-byte HMAC truncation
    phash_prefix: int           # integer, lower 48 bits of pHash


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode(
    model_id_idx: int,
    timestamp: int,
    session_token: bytes,
    phash_int: int,
) -> bytes:
    """
    Encode provenance fields into a 32-byte (256-bit) Reed-Solomon codeword.

    Args:
        model_id_idx: Integer 0-255 representing the registered model.
        timestamp:    Unix epoch integer (e.g., int(datetime.utcnow().timestamp())).
        session_token: 8-byte HMAC truncation identifying the API session.
        phash_int:    64-bit integer perceptual hash of the original image.

    Returns:
        32-byte bytes object (20 data bytes + 12 ECC bytes).

    Raises:
        ValueError: If any argument is out of range.
    """
    if not (0 <= model_id_idx <= 255):
        raise ValueError(f"model_id_idx must be 0–255, got {model_id_idx}")
    if not (0 <= timestamp <= 0xFF_FFFF_FFFF):
        raise ValueError(f"timestamp out of 40-bit range: {timestamp}")
    if len(session_token) < 8:
        raise ValueError("session_token must be at least 8 bytes")

    # Build 20-byte data section
    phash_prefix = phash_int & 0xFFFF_FFFF_FFFF   # keep lower 48 bits

    data = struct.pack(
        ">BQ6s",                                  # 1 + 8 + (skip 3 high bytes of timestamp) -> adjust
        model_id_idx,
        timestamp,                                # 8 bytes, but we only use 5; pack full then slice
        phash_prefix.to_bytes(6, "big"),
    )
    # struct ">BQ6s" gives 1+8+6=15 bytes; we need to preserve only 5 timestamp bytes.
    # Re-pack cleanly:
    data = (
        struct.pack("B", model_id_idx)            # 1 byte
        + timestamp.to_bytes(5, "big")            # 5 bytes
        + session_token[:8]                        # 8 bytes
        + phash_prefix.to_bytes(6, "big")         # 6 bytes
    )                                              # Total = 20 bytes

    assert len(data) == DATA_BYTES, f"data section size mismatch: {len(data)}"

    # Encode with Reed-Solomon → 20 + 12 = 32 bytes
    encoded = bytes(_RS.encode(bytearray(data)))

    logger.debug(
        "Encoded payload: model_id_idx=%d timestamp=%d phash_prefix=0x%012x",
        model_id_idx, timestamp, phash_prefix,
    )
    return encoded


def decode(codeword: bytes) -> DecodedPayload:
    """
    Decode a 32-byte codeword, applying ECC correction.

    Args:
        codeword: 32-byte bytes object (output from encode(), possibly with errors).

    Returns:
        DecodedPayload with the recovered fields.

    Raises:
        PayloadDecodeError: If ECC correction fails (too many errors) or length is wrong.
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

    model_id_idx = struct.unpack("B", data[0:1])[0]
    timestamp    = int.from_bytes(data[1:6], "big")
    session_token = data[6:14]
    phash_prefix  = int.from_bytes(data[14:20], "big")

    logger.debug(
        "Decoded payload: model_id_idx=%d timestamp=%d phash_prefix=0x%012x",
        model_id_idx, timestamp, phash_prefix,
    )
    return DecodedPayload(
        model_id_idx=model_id_idx,
        timestamp=timestamp,
        session_token=session_token,
        phash_prefix=phash_prefix,
    )


# ---------------------------------------------------------------------------
# Helper: generate a session token from a user id + nonce
# ---------------------------------------------------------------------------

def make_session_token(user_id: str, nonce: str, secret: bytes) -> bytes:
    """
    Derive an 8-byte session token via HMAC-SHA256(secret, user_id|nonce).

    Args:
        user_id: Caller's identifier string.
        nonce:   Random nonce string (e.g., UUID).
        secret:  HMAC secret key bytes (from env).

    Returns:
        First 8 bytes of HMAC-SHA256 digest.
    """
    message = f"{user_id}:{nonce}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return digest[:8]
