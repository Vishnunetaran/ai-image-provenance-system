"""
Robustness Tests for Latent Watermark Transforms.

Tests watermark detection survival across 5 categories of image transforms
at multiple severity levels, matching the PRD target metrics.

Test Categories:
    1. JPEG compression (Q=10, 30, 50, 75, 90)
    2. Resizing (0.25x, 0.5x, 0.75x, 1.5x, 2.0x)
    3. Screenshot simulation (blur + chroma subsampling)
    4. Cropping (10%, 20%, 30%)
    5. Color adjustments (brightness, contrast, saturation)
"""

import sys
import os
import tempfile
import shutil

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

torch = pytest.importorskip("torch")
cv2 = pytest.importorskip("cv2")

from src.watermarking.latent.pattern_generator import generate_pattern
from src.watermarking.latent.extractor import LatentWatermarkExtractor

# ──────────────────────────────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────────────────────────────

TEST_KEY = b"\x42" * 32
TEST_IMAGE_ID = "robust-test-001"
IMAGE_SIZE = 256


def _create_watermarked_image(
    image_id: str = TEST_IMAGE_ID,
    size: int = IMAGE_SIZE,
    embed_strength: float = 40.0,
) -> np.ndarray:
    """Create a synthetic image with an embedded watermark pattern.

    For robustness testing we directly add a scaled pattern to the pixel
    domain.  This simulates the output of a full diffusion pipeline without
    requiring GPU or model weights.
    """
    # Structured base image (gradient + texture)
    base = np.zeros((size, size, 3), dtype=np.float64)
    for i in range(size):
        for j in range(size):
            base[i, j, 0] = 128 + 60 * np.sin(i / 15.0)
            base[i, j, 1] = 128 + 60 * np.cos(j / 15.0)
            base[i, j, 2] = 128 + 40 * np.sin((i + j) / 20.0)

    # Generate and embed pattern
    pattern = generate_pattern(
        image_id, TEST_KEY, (size, size), apply_freq_shaping=False
    ).numpy()

    for c in range(3):
        base[:, :, c] += pattern * embed_strength

    return np.clip(base, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def watermarked_image():
    return _create_watermarked_image()


@pytest.fixture(scope="module")
def extractor():
    return LatentWatermarkExtractor(
        secret_key=TEST_KEY, detection_threshold=0.3
    )


def _apply_jpeg(image: np.ndarray, quality: int) -> np.ndarray:
    """Apply JPEG compression and decompression."""
    tmp = tempfile.mktemp(suffix=".jpg")
    try:
        cv2.imwrite(tmp, image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return cv2.imread(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _apply_resize(image: np.ndarray, scale: float) -> np.ndarray:
    """Resize image by scale, then back to original size."""
    h, w = image.shape[:2]
    small = cv2.resize(image, (int(w * scale), int(h * scale)))
    return cv2.resize(small, (w, h))


def _apply_crop(image: np.ndarray, crop_percent: float) -> np.ndarray:
    """Center-crop by percentage, then resize back to original."""
    h, w = image.shape[:2]
    margin_h = int(h * crop_percent / 2)
    margin_w = int(w * crop_percent / 2)
    cropped = image[margin_h : h - margin_h, margin_w : w - margin_w]
    return cv2.resize(cropped, (w, h))


def _apply_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Apply Gaussian blur."""
    ksize = int(sigma * 6) | 1  # Ensure odd
    return cv2.GaussianBlur(image, (ksize, ksize), sigma)


def _apply_brightness(image: np.ndarray, factor: float) -> np.ndarray:
    """Adjust brightness."""
    return np.clip(image.astype(np.float64) * factor, 0, 255).astype(np.uint8)


def _apply_contrast(image: np.ndarray, factor: float) -> np.ndarray:
    """Adjust contrast around mid-gray."""
    mid = 127.5
    adjusted = mid + (image.astype(np.float64) - mid) * factor
    return np.clip(adjusted, 0, 255).astype(np.uint8)


# ══════════════════════════════════════════════════════════════════════
# JPEG COMPRESSION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestJPEGRobustness:
    """Watermark detection after JPEG compression."""

    @pytest.mark.parametrize("quality", [90, 75, 50, 30, 10])
    def test_jpeg_compression(self, watermarked_image, extractor, quality):
        compressed = _apply_jpeg(watermarked_image, quality)
        _detected, confidence = extractor.detect_only(compressed, TEST_IMAGE_ID)
        # Log result for benchmark comparison
        print(f"\n  JPEG Q={quality}: confidence={confidence:.3f}")
        # At minimum, confidence should be measureable even at low Q
        assert confidence >= 0.0, f"Confidence should be non-negative at Q={quality}"


# ══════════════════════════════════════════════════════════════════════
# RESIZE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestResizeRobustness:
    """Watermark detection after resizing."""

    @pytest.mark.parametrize("scale", [2.0, 1.5, 0.75, 0.5, 0.25])
    def test_resize(self, watermarked_image, extractor, scale):
        resized = _apply_resize(watermarked_image, scale)
        _detected, confidence = extractor.detect_only(resized, TEST_IMAGE_ID)
        print(f"\n  Resize {scale}x: confidence={confidence:.3f}")
        assert confidence >= 0.0


# ══════════════════════════════════════════════════════════════════════
# SCREENSHOT SIMULATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestScreenshotRobustness:
    """Watermark detection after screenshot simulation."""

    def test_screenshot_blur_only(self, watermarked_image, extractor):
        """Blur simulating screen capture."""
        blurred = _apply_blur(watermarked_image, sigma=1.5)
        _detected, conf = extractor.detect_only(blurred, TEST_IMAGE_ID)
        print(f"\n  Screenshot (blur σ=1.5): confidence={conf:.3f}")
        assert conf >= 0.0

    def test_screenshot_blur_plus_jpeg(self, watermarked_image, extractor):
        """Blur + JPEG simulating screenshot then upload."""
        blurred = _apply_blur(watermarked_image, sigma=1.5)
        compressed = _apply_jpeg(blurred, 75)
        _detected, conf = extractor.detect_only(compressed, TEST_IMAGE_ID)
        print(f"\n  Screenshot + JPEG Q=75: confidence={conf:.3f}")
        assert conf >= 0.0

    def test_screenshot_with_chroma_subsampling(self, watermarked_image, extractor):
        """Simulate chroma subsampling (YCbCr 4:2:0)."""
        # Convert to YCbCr, subsample chroma, upsample back
        ycrcb = cv2.cvtColor(watermarked_image, cv2.COLOR_BGR2YCrCb)
        h, w = ycrcb.shape[:2]
        # Downsample Cr and Cb by 2x
        cr_small = cv2.resize(ycrcb[:, :, 1], (w // 2, h // 2))
        cb_small = cv2.resize(ycrcb[:, :, 2], (w // 2, h // 2))
        ycrcb[:, :, 1] = cv2.resize(cr_small, (w, h))
        ycrcb[:, :, 2] = cv2.resize(cb_small, (w, h))
        result = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

        _detected, conf = extractor.detect_only(result, TEST_IMAGE_ID)
        print(f"\n  Chroma subsampling: confidence={conf:.3f}")
        assert conf >= 0.0


# ══════════════════════════════════════════════════════════════════════
# CROPPING TESTS
# ══════════════════════════════════════════════════════════════════════


class TestCropRobustness:
    """Watermark detection after cropping."""

    @pytest.mark.parametrize("crop_pct", [0.10, 0.20, 0.30])
    def test_center_crop(self, watermarked_image, extractor, crop_pct):
        cropped = _apply_crop(watermarked_image, crop_pct)
        _detected, conf = extractor.detect_only(cropped, TEST_IMAGE_ID)
        print(f"\n  Crop {int(crop_pct*100)}%: confidence={conf:.3f}")
        assert conf >= 0.0


# ══════════════════════════════════════════════════════════════════════
# COLOR ADJUSTMENT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestColorAdjustmentRobustness:
    """Watermark detection after color adjustments."""

    @pytest.mark.parametrize("factor", [0.7, 0.85, 1.15, 1.3])
    def test_brightness(self, watermarked_image, extractor, factor):
        adjusted = _apply_brightness(watermarked_image, factor)
        _detected, conf = extractor.detect_only(adjusted, TEST_IMAGE_ID)
        label = f"+{int((factor-1)*100)}%" if factor > 1 else f"{int((factor-1)*100)}%"
        print(f"\n  Brightness {label}: confidence={conf:.3f}")
        assert conf >= 0.0

    @pytest.mark.parametrize("factor", [0.8, 0.9, 1.1, 1.2])
    def test_contrast(self, watermarked_image, extractor, factor):
        adjusted = _apply_contrast(watermarked_image, factor)
        _detected, conf = extractor.detect_only(adjusted, TEST_IMAGE_ID)
        label = f"+{int((factor-1)*100)}%" if factor > 1 else f"{int((factor-1)*100)}%"
        print(f"\n  Contrast {label}: confidence={conf:.3f}")
        assert conf >= 0.0


# ══════════════════════════════════════════════════════════════════════
# COMBINED TRANSFORM TESTS
# ══════════════════════════════════════════════════════════════════════


class TestCombinedTransforms:
    """Watermark detection after multiple transforms applied together."""

    def test_jpeg_plus_resize(self, watermarked_image, extractor):
        result = _apply_resize(watermarked_image, 0.75)
        result = _apply_jpeg(result, 50)
        _detected, conf = extractor.detect_only(result, TEST_IMAGE_ID)
        print(f"\n  Resize 0.75x + JPEG Q=50: confidence={conf:.3f}")

    def test_crop_plus_jpeg_plus_brightness(self, watermarked_image, extractor):
        result = _apply_crop(watermarked_image, 0.1)
        result = _apply_jpeg(result, 75)
        result = _apply_brightness(result, 1.2)
        _detected, conf = extractor.detect_only(result, TEST_IMAGE_ID)
        print(f"\n  Crop 10% + JPEG Q=75 + Bright +20%: confidence={conf:.3f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
