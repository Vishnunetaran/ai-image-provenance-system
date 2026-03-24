"""
T-009: Unit tests for neural_watermark_service.py

Tests:
  - embed() returns an image of the same size
  - Round-trip embed → extract recovers payload bytes
  - PSNR is acceptable after embedding
  - extract() on unwatermarked image returns low confidence
"""
import io
import os
import pytest
import numpy as np
from PIL import Image

# Only run if invisible-watermark is installed
invisible_wm = pytest.importorskip("imwatermark", reason="invisible-watermark not installed")

from provena_flask.services import neural_watermark_service as nws
from provena_flask.services.payload_codec import encode as encode_payload


@pytest.fixture
def sample_payload() -> bytes:
    """A valid 32-byte payload."""
    return encode_payload(
        model_id_idx=7,
        timestamp=1_710_000_000,
        session_token=b"\xab\xcd\xef\x01\x23\x45\x67\x89",
        phash_int=0xA0B1_C2D3_E4F5_0607,
    )


@pytest.fixture
def rgb_image() -> Image.Image:
    """Generate a 512×512 synthetic RGB image with varied content."""
    rng = np.random.default_rng(seed=42)
    arr = rng.integers(0, 256, (512, 512, 3), dtype=np.uint8)
    # Smooth slightly to give realistic frequency content
    from PIL import ImageFilter
    img = Image.fromarray(arr)
    return img.filter(ImageFilter.GaussianBlur(radius=1))


class TestNeuralWatermarkEmbed:
    """Tests for embed() function."""

    def test_embed_returns_pil_image(self, rgb_image, sample_payload):
        """embed() must return a PIL Image."""
        result = nws.embed(rgb_image, sample_payload)
        assert isinstance(result, Image.Image)

    def test_embed_preserves_size(self, rgb_image, sample_payload):
        """The watermarked image must have the same dimensions as the original."""
        result = nws.embed(rgb_image, sample_payload)
        assert result.size == rgb_image.size

    def test_embed_wrong_payload_length_raises(self, rgb_image):
        """embed() with payload != 32 bytes must raise ValueError."""
        with pytest.raises(ValueError):
            nws.embed(rgb_image, b"\x00" * 16)

    def test_psnr_acceptable(self, rgb_image, sample_payload):
        """PSNR between original and watermarked image should be >= 28 dB."""
        watermarked = nws.embed(rgb_image, sample_payload)
        psnr = nws.compute_psnr(rgb_image, watermarked)
        assert psnr >= 28.0, f"PSNR too low: {psnr:.2f} dB (target >= 28 dB)"


class TestNeuralWatermarkExtract:
    """Tests for extract() function."""

    def test_extract_returns_tuple(self, rgb_image, sample_payload):
        """extract() must return (bytes, float) tuple."""
        watermarked = nws.embed(rgb_image, sample_payload)
        payload_out, confidence = nws.extract(watermarked)
        assert isinstance(payload_out, bytes)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

    def test_extract_round_trip(self, rgb_image, sample_payload):
        """Round-trip: extracted payload must be identical to embedded payload."""
        watermarked = nws.embed(rgb_image, sample_payload)
        payload_out, confidence = nws.extract(watermarked)
        assert confidence >= nws.CONFIDENCE_THRESHOLD, \
            f"Confidence too low: {confidence} (expected >= {nws.CONFIDENCE_THRESHOLD})"
        assert payload_out == sample_payload, \
            f"Payload mismatch: {payload_out.hex()} != {sample_payload.hex()}"

    def test_extract_plain_image_fails_ecc(self, rgb_image):
        """extract() on unwatermarked returns bits which fail ECC decode."""
        payload_out, confidence = nws.extract(rgb_image)
        # Even if confidence defaults to 0.9 for lack of soft-scores,
        # ECC decode must reject the random bytes extracted.
        from provena_flask.services.payload_codec import decode, PayloadDecodeError
        with pytest.raises(PayloadDecodeError):
            decode(payload_out)

    def test_round_trip_after_png_encode(self, rgb_image, sample_payload):
        """Round-trip must survive PNG encode/decode cycle."""
        watermarked = nws.embed(rgb_image, sample_payload)

        buf = io.BytesIO()
        watermarked.save(buf, format="PNG")
        buf.seek(0)
        reloaded = Image.open(buf).convert("RGB")

        payload_out, confidence = nws.extract(reloaded)
        assert confidence >= nws.CONFIDENCE_THRESHOLD
        assert payload_out == sample_payload
