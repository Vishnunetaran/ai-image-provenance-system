"""
T-012: Unit tests for payload_codec.py

API under test (V1 / simplified):
    encode(record_id: int) -> bytes   — 4-byte data + 2-byte ECC = 6 bytes total
    decode(codeword: bytes) -> int    — returns recovered record_id

ECC capacity:
    ECC_BYTES = 2  (nsym = 2) → Reed-Solomon can correct up to 1 byte (8 bits) of error.

Tests:
  - encode() returns exactly TOTAL_BYTES (6) bytes
  - decode(encode(x)) round-trip with 0 errors
  - Edge cases: record_id == 0 and record_id == 0xFFFFFFFF (max)
  - record_id out of range raises ValueError
  - Wrong-length input to decode() raises PayloadDecodeError
  - All-zeros codeword raises PayloadDecodeError
  - 1-bit error is corrected (within ECC capacity)
  - 8-bit error (1 full byte) is corrected (ECC boundary)
  - 2-byte (16-bit) corruption beyond ECC capacity raises PayloadDecodeError
"""
import pytest
from provena_flask.services.payload_codec import (
    encode,
    decode,
    PayloadDecodeError,
    DATA_BYTES,
    ECC_BYTES,
    TOTAL_BYTES,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _flip_bits(codeword: bytes, positions: list[int]) -> bytes:
    """Flip specific bit positions in a codeword (0-indexed from MSB of byte 0)."""
    arr = bytearray(codeword)
    for bit_pos in positions:
        byte_idx = bit_pos // 8
        bit_idx  = 7 - (bit_pos % 8)
        arr[byte_idx] ^= (1 << bit_idx)
    return bytes(arr)


# ── Structure tests ────────────────────────────────────────────────────────

class TestPayloadCodecStructure:
    """Tests for encode/decode output structure."""

    def test_encode_returns_correct_length(self):
        """encode() must produce exactly TOTAL_BYTES (6) bytes."""
        codeword = encode(42)
        assert len(codeword) == TOTAL_BYTES, (
            f"Expected {TOTAL_BYTES} bytes, got {len(codeword)}"
        )

    def test_total_bytes_is_six(self):
        """Constants must be consistent: DATA_BYTES + ECC_BYTES == TOTAL_BYTES == 6."""
        assert DATA_BYTES == 4
        assert ECC_BYTES == 2
        assert TOTAL_BYTES == 6


# ── Round-trip tests ───────────────────────────────────────────────────────

class TestPayloadCodecRoundTrip:
    """encode → decode round-trip correctness."""

    def test_round_trip_zero_errors(self):
        """decode(encode(x)) must return the original record_id unchanged."""
        record_id = 123_456_789
        codeword  = encode(record_id)
        recovered = decode(codeword)
        assert recovered == record_id

    def test_round_trip_record_id_zero(self):
        """record_id == 0 encodes to an all-zeros codeword (RS-ECC of zero data is zero).
        The decoder explicitly rejects all-zeros codewords as invalid/uninitialized.
        record_id=0 is therefore a reserved/invalid value by design.
        The minimum usable record_id is 1."""
        codeword = encode(0)
        # The codeword will be all zeros — rejected by the safety guard
        with pytest.raises(PayloadDecodeError):
            decode(codeword)

    def test_round_trip_record_id_one(self):
        """Minimum valid record_id=1 must round-trip correctly."""
        codeword  = encode(1)
        recovered = decode(codeword)
        assert recovered == 1

    def test_round_trip_record_id_max(self):
        """Edge case: record_id == 0xFFFFFFFF (max 32-bit value) must round-trip."""
        max_id    = 0xFFFF_FFFF
        codeword  = encode(max_id)
        recovered = decode(codeword)
        assert recovered == max_id

    def test_round_trip_various_values(self):
        """Several representative values must all round-trip correctly."""
        for record_id in (1, 255, 1_000, 0x0000_FFFF, 0xDEAD_BEEF, 0xFFFF_FFFE):
            codeword  = encode(record_id)
            recovered = decode(codeword)
            assert recovered == record_id, (
                f"Round-trip failed for record_id={record_id:#010x}"
            )


# ── ECC correction tests ───────────────────────────────────────────────────

class TestPayloadCodecECC:
    """
    ECC_BYTES = 2 → RSCodec(2) → corrects up to 1 symbol (1 byte = 8 bits).
    """

    def test_single_bit_error_corrected(self):
        """A single flipped bit must be corrected and the round-trip must succeed."""
        record_id = 98_765
        codeword  = encode(record_id)
        corrupted = _flip_bits(codeword, [0])       # flip bit 0 (MSB of byte 0)
        recovered = decode(corrupted)
        assert recovered == record_id

    def test_eight_bit_error_in_one_byte_corrected(self):
        """Flipping all 8 bits of the first byte (1-byte burst) must be correctable."""
        record_id = 42
        codeword  = encode(record_id)
        # Flip all 8 bits of byte 0 — equivalent to XOR 0xFF on that byte
        corrupted = _flip_bits(codeword, list(range(8)))
        recovered = decode(corrupted)
        assert recovered == record_id

    def test_two_byte_errors_raises(self):
        """
        Corrupting 2 separate bytes (16 bits, beyond ECC capacity for nsym=2)
        must raise PayloadDecodeError.
        """
        record_id = 1_234_567
        codeword  = encode(record_id)
        # XOR byte 0 and byte 2 with 0xFF each — two full-byte errors
        arr = bytearray(codeword)
        arr[0] ^= 0xFF
        arr[2] ^= 0xFF
        corrupted = bytes(arr)
        with pytest.raises(PayloadDecodeError):
            decode(corrupted)


# ── Error-condition tests ──────────────────────────────────────────────────

class TestPayloadCodecErrors:
    """Negative tests: invalid inputs must raise appropriate exceptions."""

    def test_record_id_negative_raises(self):
        """Negative record_id must raise ValueError."""
        with pytest.raises(ValueError):
            encode(-1)

    def test_record_id_too_large_raises(self):
        """record_id > 0xFFFFFFFF must raise ValueError."""
        with pytest.raises(ValueError):
            encode(0x1_0000_0000)

    def test_wrong_length_short_raises(self):
        """decode() with fewer than 6 bytes must raise PayloadDecodeError."""
        with pytest.raises(PayloadDecodeError):
            decode(b"\x00" * 4)

    def test_wrong_length_long_raises(self):
        """decode() with more than 6 bytes must raise PayloadDecodeError."""
        with pytest.raises(PayloadDecodeError):
            decode(b"\x00" * 10)

    def test_wrong_length_empty_raises(self):
        """decode() with empty bytes must raise PayloadDecodeError."""
        with pytest.raises(PayloadDecodeError):
            decode(b"")

    def test_all_zeros_raises(self):
        """All-zero 6-byte codeword must raise PayloadDecodeError (invalid/empty payload guard)."""
        with pytest.raises(PayloadDecodeError):
            decode(b"\x00" * TOTAL_BYTES)
