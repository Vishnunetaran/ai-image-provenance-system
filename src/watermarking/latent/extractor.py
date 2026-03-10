"""
Watermark Extractor Module for Latent Space Watermarks.

Detects and extracts Tree-Ring style watermarks from images by correlating
with known patterns in the frequency domain.  Supports both:
    - Full extraction: recover the 160-bit payload (UUID + timestamp)
    - Detection only: fast yes/no check with confidence score

Extraction Pipeline:
    1. Convert image to frequency domain (DWT + FFT)
    2. Generate candidate pattern from secret key
    3. Compute normalised cross-correlation at multiple scales
    4. Aggregate correlation scores across scales
    5. If above threshold: extract coded bits and decode payload

References:
    - Tree-Ring Watermarks (Wen et al., 2024)
    - Normalised cross-correlation for template matching
"""

import math
import logging
from typing import Tuple, Optional, Dict, Union

import numpy as np

try:
    import torch
except ImportError:
    torch = None

try:
    import cv2
except ImportError:
    cv2 = None

from src.watermarking.latent.pattern_generator import (
    generate_pattern,
    generate_multi_scale_patterns,
)
from src.watermarking.latent.payload import decode_payload, FULL_PAYLOAD_BITS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

DEFAULT_DETECTION_THRESHOLD = 0.65
RING_RADIUS_MIN = 5
RING_RADIUS_MAX = 20
LATENT_CHANNELS = 4  # Stable Diffusion latent channels
LATENT_SCALE = 8  # Image pixels / latent pixels


class LatentWatermarkExtractor:
    """Extract and verify Tree-Ring watermarks from images.

    Performs multi-scale correlation between the image's frequency-domain
    representation and keyed watermark patterns to detect and recover
    embedded provenance data.

    Attributes:
        secret_key: 32-byte key used during watermark embedding.
        threshold: Minimum correlation score to declare detection.
        scales: Scale fractions used during embedding (must match injector).

    Example:
        >>> extractor = LatentWatermarkExtractor(secret_key=b"\\x00" * 32)
        >>> # Load an image (numpy BGR or torch tensor)
        >>> import cv2
        >>> image = cv2.imread("watermarked.png")
        >>> result, confidence = extractor.extract(image)
        >>> if result is not None:
        ...     print(f"Detected! UUID={result['image_id']}, confidence={confidence:.2f}")
    """

    def __init__(
        self,
        secret_key: bytes,
        detection_threshold: float = DEFAULT_DETECTION_THRESHOLD,
        scales: Optional[list] = None,
    ):
        """Initialise the watermark extractor.

        Args:
            secret_key: 32-byte secret key (must match the injector's key).
            detection_threshold: Minimum correlation score for detection
                (default 0.65; range [0.0, 1.0]).
            scales: Scale fractions used during embedding.
                Default: [0.5, 0.25, 0.125].

        Raises:
            ValueError: If secret_key is not 32 bytes.
        """
        if len(secret_key) != 32:
            raise ValueError(f"Secret key must be 32 bytes, got {len(secret_key)}")

        self.secret_key = secret_key
        self.threshold = detection_threshold
        self.scales = scales or [0.5, 0.25, 0.125]

        logger.info(
            f"LatentWatermarkExtractor initialised: "
            f"threshold={detection_threshold}, scales={self.scales}"
        )

    def _image_to_numpy(
        self, image: Union[np.ndarray, "torch.Tensor"]
    ) -> np.ndarray:
        """Convert various image formats to (H, W, C) uint8 numpy array.

        Args:
            image: Input image as numpy array or torch tensor.

        Returns:
            (H, W, C) numpy array in BGR format, dtype uint8.
        """
        if torch is not None and isinstance(image, torch.Tensor):
            img = image.detach().cpu().numpy()
            if img.ndim == 4:
                img = img[0]  # Take first batch element
            if img.ndim == 3 and img.shape[0] in (1, 3, 4):
                img = np.transpose(img, (1, 2, 0))  # CHW → HWC
            if img.max() <= 1.0:
                img = (img * 255).astype(np.uint8)
            else:
                img = img.astype(np.uint8)
            return img

        if isinstance(image, np.ndarray):
            if image.ndim == 2:
                # Grayscale → 3-channel
                return np.stack([image, image, image], axis=-1)
            return image.astype(np.uint8)

        raise TypeError(f"Unsupported image type: {type(image)}")

    def _extract_frequency_features(
        self, image: np.ndarray
    ) -> np.ndarray:
        """Extract frequency-domain features from an image.

        Converts the image to grayscale, applies DWT decomposition, then
        computes FFT on each subband to obtain a frequency-domain
        representation suitable for correlation with watermark patterns.

        Args:
            image: (H, W, C) BGR image.

        Returns:
            Complex-valued 2-D array of frequency features.
        """
        # Convert to grayscale float
        if image.ndim == 3:
            gray = np.mean(image.astype(np.float64), axis=2)
        else:
            gray = image.astype(np.float64)

        # Normalise to zero mean
        gray = gray - gray.mean()

        # Compute 2-D FFT
        freq = np.fft.fft2(gray)
        freq_shifted = np.fft.fftshift(freq)

        return freq_shifted

    def _compute_ring_correlation(
        self,
        freq_features: np.ndarray,
        image_id: str,
        ring_min: int = RING_RADIUS_MIN,
        ring_max: int = RING_RADIUS_MAX,
    ) -> float:
        """Compute correlation in the ring region of FFT space.

        This is the core Tree-Ring detection: check if the ring region
        of the image's FFT matches the expected keyed pattern.

        Args:
            freq_features: Shifted FFT of the image (complex 2-D array).
            image_id: Candidate image identifier to generate expected pattern.
            ring_min: Inner ring radius.
            ring_max: Outer ring radius.

        Returns:
            Normalised correlation score in [0, 1].
        """
        H, W = freq_features.shape

        # Create ring mask
        cy, cx = H // 2, W // 2
        y_coords = np.arange(H) - cy
        x_coords = np.arange(W) - cx
        yy, xx = np.meshgrid(y_coords, x_coords, indexing="ij")
        radius = np.sqrt(xx**2 + yy**2)
        ring_mask = (radius >= ring_min) & (radius <= ring_max)

        if ring_mask.sum() == 0:
            return 0.0

        # Extract ring coefficients from image
        image_ring = freq_features[ring_mask]

        # Generate expected pattern and compute its FFT ring
        pattern_np = generate_pattern(
            image_id=image_id,
            secret_key=self.secret_key,
            shape=(H, W),
            apply_freq_shaping=False,
        )
        if torch is not None:
            pattern_np = pattern_np.numpy()

        pattern_fft = np.fft.fftshift(np.fft.fft2(pattern_np))
        pattern_ring = pattern_fft[ring_mask]

        # Normalised cross-correlation (magnitude)
        img_mag = np.abs(image_ring)
        pat_mag = np.abs(pattern_ring)

        num = np.sum(img_mag * pat_mag)
        den = np.sqrt(np.sum(img_mag**2) * np.sum(pat_mag**2))

        if den < 1e-10:
            return 0.0

        correlation = float(num / den)
        return min(max(correlation, 0.0), 1.0)

    def _compute_spatial_correlation(
        self,
        image: np.ndarray,
        image_id: str,
    ) -> float:
        """Compute spatial-domain correlation across multiple DWT scales.

        Generates multi-scale patterns and correlates each with the
        corresponding DWT subband of the image.

        Args:
            image: (H, W, C) BGR image.
            image_id: Candidate image identifier.

        Returns:
            Average correlation score across scales, in [0, 1].
        """
        # Convert to grayscale float
        if image.ndim == 3:
            gray = np.mean(image.astype(np.float64), axis=2)
        else:
            gray = image.astype(np.float64)

        H, W = gray.shape

        try:
            import pywt
        except ImportError:
            logger.warning("pywt not available; using FFT-only correlation")
            return 0.0

        correlations = []

        for i, scale in enumerate(self.scales):
            # Decompose image
            coeffs = pywt.dwt2(gray, "haar")
            cA, (cH, cV, cD) = coeffs

            # Generate pattern at this scale's subband size
            pattern = generate_pattern(
                image_id=image_id,
                secret_key=self.secret_key,
                shape=cH.shape,
                apply_freq_shaping=True,
            )
            if torch is not None:
                pattern = pattern.numpy()

            # Correlate with horizontal detail subband
            flat_img = cH.flatten()
            flat_pat = pattern.flatten()

            # Normalised cross-correlation
            img_centered = flat_img - flat_img.mean()
            pat_centered = flat_pat - flat_pat.mean()

            num = np.sum(img_centered * pat_centered)
            den = np.sqrt(np.sum(img_centered**2) * np.sum(pat_centered**2))

            if den > 1e-10:
                corr = abs(float(num / den))
                correlations.append(corr)

            # Go deeper: use approximation for next level
            gray = cA

        if not correlations:
            return 0.0

        return float(np.mean(correlations))

    def detect_only(
        self,
        image: Union[np.ndarray, "torch.Tensor"],
        image_id: str,
    ) -> Tuple[bool, float]:
        """Fast watermark detection without payload extraction.

        Computes correlation scores in both frequency and spatial domains
        and returns a combined confidence score.

        Args:
            image: Input image (BGR numpy array or torch tensor).
            image_id: Expected image identifier (for pattern generation).

        Returns:
            Tuple of (is_watermarked, confidence).
            ``is_watermarked`` is True if confidence exceeds the threshold.

        Example:
            >>> extractor = LatentWatermarkExtractor(secret_key=b"\\x00" * 32)
            >>> is_wm, conf = extractor.detect_only(image, "img-001")
            >>> print(f"Watermark detected: {is_wm} (confidence={conf:.2f})")
        """
        img_np = self._image_to_numpy(image)

        # Ring correlation (Tree-Ring style)
        freq_features = self._extract_frequency_features(img_np)
        ring_score = self._compute_ring_correlation(freq_features, image_id)

        # Spatial correlation (DWT-based)
        spatial_score = self._compute_spatial_correlation(img_np, image_id)

        # Combined score (weighted: ring detection is primary)
        combined = 0.7 * ring_score + 0.3 * spatial_score

        is_detected = combined >= self.threshold

        logger.info(
            f"Watermark detection: ring={ring_score:.3f}, "
            f"spatial={spatial_score:.3f}, combined={combined:.3f}, "
            f"detected={is_detected}"
        )

        return is_detected, combined

    def extract(
        self,
        image: Union[np.ndarray, "torch.Tensor"],
        candidate_ids: Optional[list] = None,
    ) -> Tuple[Optional[Dict], float]:
        """Extract watermark payload from an image.

        Attempts to detect and decode the watermark.  If ``candidate_ids``
        are provided, tests each one and returns the best match.  Otherwise,
        performs a blind detection using the frequency-domain signature.

        Args:
            image: Input image (BGR numpy array or torch tensor).
            candidate_ids: Optional list of image IDs to test against.
                If None, performs blind correlation-based detection.

        Returns:
            Tuple of (payload_dict, confidence).
            ``payload_dict`` has keys {"image_id", "timestamp"} on success,
            or None on failure.

        Example:
            >>> extractor = LatentWatermarkExtractor(secret_key=b"\\x00" * 32)
            >>> result, conf = extractor.extract(image, candidate_ids=["img-001", "img-002"])
            >>> if result:
            ...     print(f"Match: {result['image_id']}, ts={result['timestamp']}")
        """
        img_np = self._image_to_numpy(image)

        if candidate_ids:
            # Test each candidate
            best_id = None
            best_confidence = 0.0

            for cid in candidate_ids:
                detected, confidence = self.detect_only(img_np, cid)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_id = cid

            if best_confidence >= self.threshold and best_id is not None:
                result = {
                    "image_id": best_id,
                    "timestamp": None,  # Cannot recover timestamp from correlation alone
                    "detection_method": "candidate_matching",
                    "confidence": best_confidence,
                }
                logger.info(
                    f"Watermark extracted via candidate matching: "
                    f"id={best_id}, confidence={best_confidence:.3f}"
                )
                return result, best_confidence

        # Blind detection: try to extract coded bits from frequency domain
        blind_result = self._blind_extract(img_np)
        if blind_result is not None:
            return blind_result

        logger.info("No watermark detected")
        return None, 0.0

    def _blind_extract(
        self, image: np.ndarray
    ) -> Optional[Tuple[Dict, float]]:
        """Attempt blind watermark extraction without candidate IDs.

        Uses the frequency-domain magnitude pattern to try to recover
        the embedded coded bits directly.

        Args:
            image: (H, W, C) BGR image.

        Returns:
            Tuple of (payload_dict, confidence) or None.
        """
        # Convert to grayscale and compute FFT
        if image.ndim == 3:
            gray = np.mean(image.astype(np.float64), axis=2)
        else:
            gray = image.astype(np.float64)

        H, W = gray.shape

        # FFT and extract ring region
        freq = np.fft.fftshift(np.fft.fft2(gray - gray.mean()))

        cy, cx = H // 2, W // 2
        y_coords = np.arange(H) - cy
        x_coords = np.arange(W) - cx
        yy, xx = np.meshgrid(y_coords, x_coords, indexing="ij")
        radius = np.sqrt(xx**2 + yy**2)
        ring_mask = (radius >= RING_RADIUS_MIN) & (radius <= RING_RADIUS_MAX)

        ring_magnitudes = np.abs(freq[ring_mask])

        if len(ring_magnitudes) == 0:
            return None

        # Compute overall energy ratio in ring vs. total (watermark indicator)
        total_energy = np.sum(np.abs(freq) ** 2)
        ring_energy = np.sum(ring_magnitudes**2)

        if total_energy < 1e-10:
            return None

        energy_ratio = ring_energy / total_energy

        # Heuristic: watermarked images have elevated ring energy
        # (This is a soft indicator, not a full payload extraction)
        ring_confidence = min(energy_ratio * 100, 1.0)  # Scale to [0, 1]

        if ring_confidence < self.threshold * 0.5:
            return None

        # Try to extract phase pattern for bit recovery
        ring_phases = np.angle(freq[ring_mask])
        num_coded_bits = min(len(ring_phases), FULL_PAYLOAD_BITS * 5)

        # Quantise phases to bits
        coded_bits = (ring_phases > 0).astype(np.int8)[:num_coded_bits]

        if len(coded_bits) < FULL_PAYLOAD_BITS:
            # Not enough bits for full payload
            return None

        # Attempt payload decoding
        uuid_str, timestamp = decode_payload(coded_bits)

        if uuid_str is not None:
            result = {
                "image_id": uuid_str,
                "timestamp": timestamp,
                "detection_method": "blind_extraction",
                "confidence": float(ring_confidence),
            }
            logger.info(
                f"Blind watermark extraction: uuid={uuid_str}, "
                f"confidence={ring_confidence:.3f}"
            )
            return result, ring_confidence

        return None

    def get_detection_stats(
        self,
        image: Union[np.ndarray, "torch.Tensor"],
        image_id: str,
    ) -> Dict:
        """Get detailed detection statistics for debugging.

        Args:
            image: Input image.
            image_id: Expected image identifier.

        Returns:
            Dictionary with ring_correlation, spatial_correlation,
            combined_score, is_detected, and threshold.
        """
        img_np = self._image_to_numpy(image)
        freq = self._extract_frequency_features(img_np)

        ring_score = self._compute_ring_correlation(freq, image_id)
        spatial_score = self._compute_spatial_correlation(img_np, image_id)
        combined = 0.7 * ring_score + 0.3 * spatial_score

        return {
            "ring_correlation": float(ring_score),
            "spatial_correlation": float(spatial_score),
            "combined_score": float(combined),
            "is_detected": combined >= self.threshold,
            "threshold": self.threshold,
        }
