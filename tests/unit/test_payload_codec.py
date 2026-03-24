"""
T-012: Unit tests for payload_codec.py

Tests:
  - Round-trip encode → decode with 0 injected bit errors
  - Round-trip with 10 injected bit errors (within ECC correction capacity)
  - Round-trip with 30 injected bit errors (at ECC correction limit)
  - Failure at 31+ injected bit errors (beyond ECC capacity)
  - Edge cases: min/max values for model_id_idx and timestamp
"""
import struct
import pytest
from provena_flask.services.payload_codec import (
    encode,
    decode,
    PayloadDecodeError,
    DATA_BYTES,
    TOTAL_BYTES,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def sample_args():
    """Valid sample arguments for encode()."""
    return {
        "model_id_idx":  42,
        "timestamp":     1_710_000_000,         # ~March 2024
        "session_token": b"\xde\xad\xbe\xef\xca\xfe\xba\xbe",
        "phash_int":     0xA3F5_C8D2_E1B4_F7A9,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _flip_bits(codeword: bytes, positions: list[int]) -> bytes:
    """Flip specific bit positions in a codeword (0-indexed, MSB-first)."""
    arr = bytearray(codeword)
    for bit_pos in positions:
        byte_idx = bit_pos // 8
        bit_idx  = 7 - (bit_pos % 8)
        arr[byte_idx] ^= (1 << bit_idx)
    return bytes(arr)


def _inject_errors(codeword: bytes, n_bits: int) -> bytes:
    """Inject n_bits errors at evenly-spaced positions."""
    total_bits = len(codeword) * 8
    step = max(1, total_bits // n_bits)
    positions = [i * step for i in range(n_bits)][:n_bits]
    return _flip_bits(codeword, positions)


# ── Tests ──────────────────────────────────────────────────────────────────

class TestPayloadCodecRoundTrip:
    """Round-trip encode/decode tests."""

    def test_encode_returns_correct_length(self, sample_args):
        """encode() must produce exactly TOTAL_BYTES (32) bytes."""
        codeword = encode(**sample_args)
        assert len(codeword) == TOTAL_BYTES, \
            f"Expected {TOTAL_BYTES} bytes, got {len(codeword)}"

    def test_round_trip_zero_errors(self, sample_args):
        """decode(encode(x)) must return identical fields with no errors."""
        codeword = encode(**sample_args)
        result = decode(codeword)

        assert result.model_id_idx  == sample_args["model_id_idx"]
        assert result.timestamp     == sample_args["timestamp"]
        assert result.session_token == sample_args["session_token"][:8]
        # phash_prefix is stored as lower 48 bits of phash_int
        expected_prefix = sample_args["phash_int"] & 0xFFFF_FFFF_FFFF
        assert result.phash_prefix  == expected_prefix

    def test_round_trip_ten_bit_errors(self, sample_args):
        """decode() must correct up to 10 bit errors (well within ECC capacity)."""
        codeword  = encode(**sample_args)
        arr = bytearray(codeword)
        for bit_pos in range(10, 20):  # 10 contiguous bits
            byte_idx = bit_pos // 8
            bit_idx  = 7 - (bit_pos % 8)
            arr[byte_idx] ^= (1 << bit_idx)
        corrupted = bytes(arr)

        result = decode(corrupted)
        assert result.model_id_idx == sample_args["model_id_idx"]
        assert result.timestamp    == sample_args["timestamp"]

    def test_round_trip_thirty_bit_errors(self, sample_args):
        """decode() must correct up to 30 burst bit errors (ECC design target)."""
        codeword  = encode(**sample_args)
        # Inject 30 errors concentrated in a burst (worst case)
        arr = bytearray(codeword)
        # Flip 30 consecutive bits starting from bit 10
        for bit_pos in range(10, 40):
            byte_idx = bit_pos // 8
            bit_idx  = 7 - (bit_pos % 8)
            arr[byte_idx] ^= (1 << bit_idx)
        corrupted = bytes(arr)

        result = decode(corrupted)
        assert result.model_id_idx == sample_args["model_id_idx"]
        assert result.timestamp    == sample_args["timestamp"]

    def test_failure_beyond_thirty_one_errors(self, sample_args):
        """decode() must raise PayloadDecodeError when too many errors are injected."""
        codeword  = encode(**sample_args)
        corrupted = _inject_errors(codeword, 40)   # 40 errors → beyond ECC capacity

        with pytest.raises(PayloadDecodeError):
            decode(corrupted)

    def test_wrong_length_raises(self):
        """decode() must raise PayloadDecodeError on wrong-length input."""
        with pytest.raises(PayloadDecodeError):
            decode(b"\x00" * 10)

    def test_all_zeros_payload_decodes(self):
        """All-zero codeword should raise PayloadDecodeError (not crash)."""
        with pytest.raises(PayloadDecodeError):
            decode(b"\x00" * TOTAL_BYTES)


class TestPayloadCodecEdgeCases:
    """Edge cases and boundary values."""

    def test_min_model_id_idx(self):
        """model_id_idx = 0 must encode and decode correctly."""
        cw = encode(0, 1_000_000, b"\x01" * 8, 0xFFFF_FFFF_FFFF_FFFF)
        r  = decode(cw)
        assert r.model_id_idx == 0

    def test_max_model_id_idx(self):
        """model_id_idx = 255 must encode and decode correctly."""
        cw = encode(255, 1_000_000, b"\x01" * 8, 0x0000_0000_0000_0001)
        r  = decode(cw)
        assert r.model_id_idx == 255

    def test_invalid_model_id_idx(self):
        """model_id_idx outside 0-255 must raise ValueError."""
        with pytest.raises(ValueError):
            encode(256, 1_000_000, b"\x01" * 8, 0)

    def test_max_timestamp(self):
        """Timestamp at 40-bit max must encode correctly."""
        max_ts = 0xFF_FFFF_FFFF  # ~year 2109
        cw = encode(1, max_ts, b"\x01" * 8, 12345)
        r  = decode(cw)
        assert r.timestamp == max_ts

    def test_phash_prefix_truncation(self):
        """phash_prefix stores only the lower 48 bits of phash_int."""
        phash_int = 0xDEAD_BEEF_CAFE_1234
        expected_prefix = phash_int & 0xFFFF_FFFF_FFFF
        cw = encode(1, 1_000_000, b"\x01" * 8, phash_int)
        r  = decode(cw)
        assert r.phash_prefix == expected_prefix
