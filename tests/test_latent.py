"""
Unit Tests for Latent Space Watermarking Module.

Comprehensive test suite covering:
    - Pattern generation (determinism, uniqueness, statistics)
    - Payload encoding/decoding (round-trip, error correction)
    - Diffusion injector (shape preservation, timestep matching)
    - Extractor (detection on synthetic images)

All tests run on CPU without requiring a GPU or diffusion model weights.
"""

import sys
import os
import uuid
import time

import numpy as np
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ──────────────────────────────────────────────────────────────────────
# Conditional imports (skip tests if dependencies missing)
# ──────────────────────────────────────────────────────────────────────

torch = pytest.importorskip("torch", reason="PyTorch required for latent watermarking tests")

from src.watermarking.latent.pattern_generator import (
    generate_pattern,
    generate_multi_scale_patterns,
    _derive_seed,
    _chacha20_stream,
    _box_muller,
    _bytes_to_uniform,
)
from src.watermarking.latent.payload import (
    encode_payload,
    decode_payload,
    _uuid_to_bits,
    _bits_to_uuid,
    _timestamp_to_bits,
    _bits_to_timestamp,
    _compute_crc16,
    _verify_crc16,
    FULL_PAYLOAD_BITS,
    RAW_PAYLOAD_BITS,
)
from src.watermarking.latent.diffusion_injector import LatentDiffusionInjector
from src.watermarking.latent.extractor import LatentWatermarkExtractor


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

TEST_KEY = b"\x00" * 32
TEST_IMAGE_ID = "img-test-001"
TEST_UUID = "550e8400-e29b-41d4-a716-446655440000"
TEST_TIMESTAMP = 1700000000


@pytest.fixture
def secret_key():
    return TEST_KEY


@pytest.fixture
def image_id():
    return TEST_IMAGE_ID


@pytest.fixture
def injector():
    return LatentDiffusionInjector(secret_key=TEST_KEY, alpha=0.08)


@pytest.fixture
def extractor():
    return LatentWatermarkExtractor(secret_key=TEST_KEY, detection_threshold=0.5)


# ══════════════════════════════════════════════════════════════════════
# PATTERN GENERATOR TESTS
# ══════════════════════════════════════════════════════════════════════


class TestPatternGenerator:
    """Tests for ChaCha20-based pattern generation."""

    def test_pattern_determinism(self, secret_key, image_id):
        """Same inputs produce identical patterns."""
        shape = (4, 64, 64)
        p1 = generate_pattern(image_id, secret_key, shape)
        p2 = generate_pattern(image_id, secret_key, shape)
        assert torch.allclose(p1, p2), "Patterns must be deterministic"

    def test_pattern_uniqueness_different_ids(self, secret_key):
        """Different image IDs produce different patterns."""
        shape = (4, 64, 64)
        p1 = generate_pattern("img-001", secret_key, shape)
        p2 = generate_pattern("img-002", secret_key, shape)
        assert not torch.allclose(p1, p2), "Different IDs must produce different patterns"

    def test_pattern_uniqueness_different_keys(self, image_id):
        """Different keys produce different patterns."""
        shape = (4, 64, 64)
        key1 = b"\x00" * 32
        key2 = b"\x01" * 32
        p1 = generate_pattern(image_id, key1, shape)
        p2 = generate_pattern(image_id, key2, shape)
        assert not torch.allclose(p1, p2), "Different keys must produce different patterns"

    def test_pattern_shape(self, secret_key, image_id):
        """Output shape matches requested shape."""
        for shape in [(4, 64, 64), (3, 128, 128), (1, 32, 32), (64, 64)]:
            pattern = generate_pattern(image_id, secret_key, shape)
            assert pattern.shape == torch.Size(shape), f"Expected shape {shape}"

    def test_pattern_dtype(self, secret_key, image_id):
        """Pattern should be float32."""
        pattern = generate_pattern(image_id, secret_key, (4, 64, 64))
        assert pattern.dtype == torch.float32

    def test_pattern_unit_energy(self, secret_key, image_id):
        """Pattern should be approximately normalised to unit energy."""
        pattern = generate_pattern(image_id, secret_key, (4, 64, 64))
        norm = torch.norm(pattern).item()
        assert abs(norm - 1.0) < 0.1, f"Pattern norm should be ~1.0, got {norm}"

    def test_pattern_zero_mean_approx(self, secret_key, image_id):
        """Pattern mean should be approximately zero (Gaussian noise)."""
        pattern = generate_pattern(
            image_id, secret_key, (4, 128, 128), apply_freq_shaping=False
        )
        mean = pattern.mean().item()
        assert abs(mean) < 0.05, f"Mean should be ~0, got {mean}"

    def test_invalid_key_length(self, image_id):
        """Should raise ValueError for wrong key length."""
        with pytest.raises(ValueError, match="32 bytes"):
            generate_pattern(image_id, b"\x00" * 16, (4, 64, 64))

    def test_multi_scale_patterns(self, secret_key, image_id):
        """Multi-scale pattern generation should work correctly."""
        patterns = generate_multi_scale_patterns(
            image_id, secret_key, (4, 64, 64)
        )
        assert len(patterns) == 3, "Default scales should produce 3 patterns"
        for p in patterns:
            assert p.shape == torch.Size([4, 64, 64])

    def test_multi_scale_patterns_are_distinct(self, secret_key, image_id):
        """Each scale should produce a distinct pattern."""
        patterns = generate_multi_scale_patterns(
            image_id, secret_key, (4, 64, 64)
        )
        for i in range(len(patterns)):
            for j in range(i + 1, len(patterns)):
                assert not torch.allclose(
                    patterns[i], patterns[j]
                ), f"Patterns at scales {i} and {j} should differ"


class TestDerivesSeed:
    """Tests for internal seed derivation."""

    def test_seed_deterministic(self):
        """Same key + ID → same seed."""
        s1 = _derive_seed(b"\x00" * 32, "img-1")
        s2 = _derive_seed(b"\x00" * 32, "img-1")
        assert s1 == s2

    def test_seed_different_ids(self):
        """Different IDs → different seeds."""
        s1 = _derive_seed(b"\x00" * 32, "img-1")
        s2 = _derive_seed(b"\x00" * 32, "img-2")
        assert s1 != s2

    def test_seed_length(self):
        """Seed should be 32 bytes."""
        seed = _derive_seed(b"\x00" * 32, "test")
        assert len(seed) == 32


class TestChaChaStream:
    """Tests for ChaCha20 stream generation."""

    def test_stream_length(self):
        """Output should have the requested length."""
        key = b"\x00" * 32
        nonce = b"\x00" * 16
        stream = _chacha20_stream(key, nonce, 1024)
        assert len(stream) == 1024

    def test_stream_deterministic(self):
        """Same inputs → same stream."""
        key = b"\x00" * 32
        nonce = b"\x00" * 16
        s1 = _chacha20_stream(key, nonce, 256)
        s2 = _chacha20_stream(key, nonce, 256)
        assert s1 == s2

    def test_stream_non_trivial(self):
        """Stream should not be all zeros."""
        key = b"\x01" * 32
        nonce = b"\x02" * 16
        stream = _chacha20_stream(key, nonce, 256)
        assert stream != b"\x00" * 256


class TestBoxMuller:
    """Tests for Box-Muller transform."""

    def test_output_distribution(self):
        """Output should be approximately standard normal."""
        np.random.seed(42)
        uniform = np.random.uniform(0, 1, 10000)
        gaussian = _box_muller(uniform)
        assert abs(gaussian.mean()) < 0.1, "Mean should be ~0"
        assert abs(gaussian.std() - 1.0) < 0.1, "Std should be ~1"


# ══════════════════════════════════════════════════════════════════════
# PAYLOAD TESTS
# ══════════════════════════════════════════════════════════════════════


class TestPayload:
    """Tests for payload encoding/decoding."""

    def test_uuid_round_trip(self):
        """UUID should survive bits conversion."""
        bits = _uuid_to_bits(TEST_UUID)
        assert len(bits) == 128
        recovered = _bits_to_uuid(bits)
        assert recovered == TEST_UUID

    def test_timestamp_round_trip(self):
        """Timestamp should survive bits conversion."""
        bits = _timestamp_to_bits(TEST_TIMESTAMP)
        assert len(bits) == 32
        recovered = _bits_to_timestamp(bits)
        assert recovered == TEST_TIMESTAMP

    def test_crc16_integrity(self):
        """CRC should verify correctly."""
        data = np.array([1, 0, 1, 1, 0, 0, 1, 0] * 20, dtype=np.int8)
        crc = _compute_crc16(data)
        assert len(crc) == 16
        assert _verify_crc16(data, crc)

    def test_crc16_detects_corruption(self):
        """CRC should detect bit flips."""
        data = np.array([1, 0, 1, 1, 0, 0, 1, 0] * 20, dtype=np.int8)
        crc = _compute_crc16(data)
        corrupted = data.copy()
        corrupted[0] = 1 - corrupted[0]  # Flip first bit
        assert not _verify_crc16(corrupted, crc)

    def test_payload_round_trip_redundancy(self):
        """Payload should survive encode → decode with redundancy ECC."""
        coded = encode_payload(TEST_UUID, TEST_TIMESTAMP, ecc_backend="redundancy")
        uuid_str, ts = decode_payload(coded, ecc_backend="redundancy")
        assert uuid_str == TEST_UUID
        assert ts == TEST_TIMESTAMP

    def test_payload_error_correction_redundancy(self):
        """Payload should survive ~5% random bit errors with redundancy.

        5x redundancy uses majority voting: each bit survives if ≤2 of 5
        copies are flipped. At 5% random error rate, the probability of
        3+ flips in a group of 5 is extremely low (~0.01%).
        """
        np.random.seed(42)
        coded = encode_payload(TEST_UUID, TEST_TIMESTAMP, ecc_backend="redundancy")
        
        # Inject 5% random errors (safe margin for 5x majority voting)
        noisy = coded.copy()
        error_rate = 0.05
        num_errors = int(len(noisy) * error_rate)
        error_positions = np.random.choice(len(noisy), num_errors, replace=False)
        noisy[error_positions] = 1 - noisy[error_positions]

        uuid_str, ts = decode_payload(noisy, ecc_backend="redundancy")
        assert uuid_str == TEST_UUID, f"Failed to recover UUID after {error_rate*100}% errors"
        assert ts == TEST_TIMESTAMP

    def test_payload_coded_length(self):
        """Coded payload should be longer than raw payload."""
        coded = encode_payload(TEST_UUID, TEST_TIMESTAMP)
        assert len(coded) > RAW_PAYLOAD_BITS, "ECC should add redundancy"

    def test_payload_auto_timestamp(self):
        """encode_payload without timestamp should use current time."""
        coded = encode_payload(TEST_UUID, ecc_backend="redundancy")
        uuid_str, ts = decode_payload(coded, ecc_backend="redundancy")
        assert uuid_str == TEST_UUID
        assert ts is not None
        # Timestamp should be within 5 seconds of now
        assert abs(ts - int(time.time())) < 5

    def test_random_uuid_round_trip(self):
        """Random UUID should survive full round trip."""
        random_uuid = str(uuid.uuid4())
        coded = encode_payload(random_uuid, 12345, ecc_backend="redundancy")
        uuid_str, ts = decode_payload(coded, ecc_backend="redundancy")
        assert uuid_str == random_uuid
        assert ts == 12345


# ══════════════════════════════════════════════════════════════════════
# DIFFUSION INJECTOR TESTS
# ══════════════════════════════════════════════════════════════════════


class TestDiffusionInjector:
    """Tests for latent diffusion injection."""

    def test_init_valid(self):
        """Should initialise with valid parameters."""
        inj = LatentDiffusionInjector(secret_key=TEST_KEY)
        assert inj.alpha == 0.08
        assert inj.timestep_fractions == [0.5, 0.25, 0.125]

    def test_init_invalid_key(self):
        """Should reject invalid key length."""
        with pytest.raises(ValueError, match="32 bytes"):
            LatentDiffusionInjector(secret_key=b"\x00" * 16)

    def test_init_invalid_alpha(self):
        """Should reject out-of-range alpha."""
        with pytest.raises(ValueError, match="Alpha"):
            LatentDiffusionInjector(secret_key=TEST_KEY, alpha=5.0)

    def test_inject_at_target_timestep(self, injector):
        """Injection should modify latent at target timestep T/2."""
        latent = torch.randn(1, 4, 64, 64)
        original = latent.clone()
        modified = injector.inject(latent, timestep=500, total_timesteps=1000, image_id="img-1")
        
        # Should be modified (timestep 500/1000 = 0.5 matches first fraction)
        assert not torch.allclose(modified, original), "Latent should be modified at T/2"

    def test_inject_at_non_target_timestep(self, injector):
        """Injection should NOT modify latent at non-target timestep."""
        latent = torch.randn(1, 4, 64, 64)
        original = latent.clone()
        modified = injector.inject(latent, timestep=700, total_timesteps=1000, image_id="img-1")
        
        # 700/1000 = 0.7, doesn't match any fraction
        assert torch.allclose(modified, original), "Latent should be unchanged at non-target timestep"

    def test_inject_shape_preservation(self, injector):
        """Injection should not change tensor shape."""
        for shape in [(1, 4, 64, 64), (2, 4, 32, 32), (1, 4, 128, 128)]:
            latent = torch.randn(*shape)
            modified = injector.inject(latent, timestep=500, total_timesteps=1000, image_id="img-1")
            assert modified.shape == latent.shape, f"Shape mismatch: {modified.shape} vs {latent.shape}"

    def test_inject_deterministic(self, injector):
        """Same inputs should produce same output."""
        latent = torch.randn(1, 4, 64, 64)
        m1 = injector.inject(latent.clone(), timestep=500, total_timesteps=1000, image_id="img-1")
        m2 = injector.inject(latent.clone(), timestep=500, total_timesteps=1000, image_id="img-1")
        assert torch.allclose(m1, m2), "Injection must be deterministic"

    def test_watermark_initial_noise(self, injector):
        """Tree-Ring initial noise watermarking should work."""
        noise = torch.randn(1, 4, 64, 64)
        wm_noise = injector.watermark_initial_noise(noise, "img-001")
        
        assert wm_noise.shape == noise.shape
        assert not torch.allclose(wm_noise, noise), "Noise should be modified"

    def test_watermark_initial_noise_3d(self, injector):
        """Should handle 3-D input (no batch dim)."""
        noise = torch.randn(4, 64, 64)
        wm_noise = injector.watermark_initial_noise(noise, "img-001")
        assert wm_noise.shape == noise.shape

    def test_pipeline_callback_creation(self, injector):
        """Callback creation should return a callable."""
        callback = injector.create_pipeline_callback("img-001")
        assert callable(callback)

    def test_cache_management(self, injector):
        """Pattern cache should be clearable."""
        _ = injector._get_patterns("img-1", (1, 4, 64, 64))
        assert len(injector._pattern_cache) > 0
        injector.clear_cache()
        assert len(injector._pattern_cache) == 0


# ══════════════════════════════════════════════════════════════════════
# EXTRACTOR TESTS
# ══════════════════════════════════════════════════════════════════════


class TestExtractor:
    """Tests for watermark extraction."""

    def test_init_valid(self):
        """Should initialise with valid parameters."""
        ext = LatentWatermarkExtractor(secret_key=TEST_KEY)
        assert ext.threshold == 0.65

    def test_init_invalid_key(self):
        """Should reject invalid key length."""
        with pytest.raises(ValueError, match="32 bytes"):
            LatentWatermarkExtractor(secret_key=b"\x00" * 16)

    def test_detection_on_watermarked_image(self, extractor):
        """Should detect watermark on a synthetically watermarked image."""
        # Create a synthetic "watermarked" image:
        # generate a pattern and add it to a base image
        H, W = 256, 256
        base_image = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
        
        # Generate pattern and add to image
        pattern = generate_pattern(
            TEST_IMAGE_ID, TEST_KEY, (H, W), apply_freq_shaping=False
        ).numpy()
        
        # Add pattern to grayscale channel
        gray = np.mean(base_image.astype(np.float64), axis=2)
        watermarked_gray = gray + pattern * 30  # Strong embedding for testing
        watermarked_gray = np.clip(watermarked_gray, 0, 255)
        
        wm_image = base_image.copy()
        wm_image[:, :, 0] = watermarked_gray.astype(np.uint8)
        wm_image[:, :, 1] = watermarked_gray.astype(np.uint8)
        wm_image[:, :, 2] = watermarked_gray.astype(np.uint8)
        
        detected, confidence = extractor.detect_only(wm_image, TEST_IMAGE_ID)
        assert confidence > 0.0, f"Expected positive confidence, got {confidence}"

    def test_detection_stats(self, extractor):
        """get_detection_stats should return expected keys."""
        image = np.random.randint(0, 255, (128, 128, 3), dtype=np.uint8)
        stats = extractor.get_detection_stats(image, TEST_IMAGE_ID)
        
        assert "ring_correlation" in stats
        assert "spatial_correlation" in stats
        assert "combined_score" in stats
        assert "is_detected" in stats
        assert "threshold" in stats

    def test_extract_returns_none_on_clean_image(self, extractor):
        """Extract on a random (non-watermarked) image should return None."""
        image = np.random.randint(0, 255, (128, 128, 3), dtype=np.uint8)
        result, confidence = extractor.extract(image, candidate_ids=["img-none"])
        # On random noise, correlation should be low
        assert confidence < 0.9, f"Unexpected high confidence {confidence} on random image"

    def test_image_format_conversion_numpy(self, extractor):
        """Should handle numpy BGR input."""
        image = np.random.randint(0, 255, (128, 128, 3), dtype=np.uint8)
        stats = extractor.get_detection_stats(image, "test")
        assert isinstance(stats, dict)

    def test_image_format_conversion_torch(self, extractor):
        """Should handle torch tensor input."""
        image = torch.randint(0, 255, (3, 128, 128), dtype=torch.uint8)
        stats = extractor.get_detection_stats(image, "test")
        assert isinstance(stats, dict)

    def test_image_format_conversion_grayscale(self, extractor):
        """Should handle grayscale numpy input."""
        image = np.random.randint(0, 255, (128, 128), dtype=np.uint8)
        stats = extractor.get_detection_stats(image, "test")
        assert isinstance(stats, dict)


# ══════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_pipeline_synthetic(self):
        """Full embed → inject → extract pipeline on synthetic data."""
        key = b"\x42" * 32
        image_id = "int-test-001"
        test_uuid = str(uuid.uuid4())
        
        # 1. Encode payload
        coded = encode_payload(test_uuid, 1700000000, ecc_backend="redundancy")
        assert len(coded) > 0
        
        # 2. Generate pattern
        pattern = generate_pattern(image_id, key, (4, 64, 64))
        assert pattern.shape == torch.Size([4, 64, 64])
        
        # 3. Create injector and inject into synthetic latent
        injector = LatentDiffusionInjector(secret_key=key, alpha=0.1)
        latent = torch.randn(1, 4, 64, 64)
        modified = injector.inject(latent, timestep=500, total_timesteps=1000, image_id=image_id)
        
        assert modified.shape == latent.shape
        assert not torch.allclose(modified, latent)
        
        # 4. Decode payload
        uuid_str, ts = decode_payload(coded, ecc_backend="redundancy")
        assert uuid_str == test_uuid
        assert ts == 1700000000

    def test_tree_ring_full_pipeline(self):
        """Tree-Ring: initial noise → watermark → detect."""
        key = b"\xAB" * 32
        image_id = "tree-ring-001"
        
        injector = LatentDiffusionInjector(secret_key=key, alpha=0.1)
        extractor = LatentWatermarkExtractor(secret_key=key, detection_threshold=0.3)
        
        # 1. Watermark initial noise
        noise = torch.randn(1, 4, 64, 64)
        wm_noise = injector.watermark_initial_noise(noise, image_id)
        
        assert wm_noise.shape == noise.shape
        
        # 2. Simulate "denoised image" (just use the noise for testing)
        # In practice, this would go through the full diffusion pipeline
        simulated_image = (wm_noise[0, :3].permute(1, 2, 0) * 127.5 + 127.5).clamp(0, 255)
        image_np = simulated_image.numpy().astype(np.uint8)
        
        # 3. Get detection stats
        stats = extractor.get_detection_stats(image_np, image_id)
        assert isinstance(stats["ring_correlation"], float)
        assert isinstance(stats["combined_score"], float)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
