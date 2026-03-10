"""
4-Layer Ensemble Detection Pipeline.

Combines all detection layers into a unified system with weighted voting:
    1. Latent watermark decoder (weight 0.4) — primary, fastest
    2. Perceptual hash lookup (weight 0.3) — fast fallback
    3. CNN style classifier (weight 0.2) — secondary
    4. ViT global detector (weight 0.1) — tertiary

Detection modes:
    - "fast":     Layers 1+2 only (≤50ms)
    - "standard": Layers 1+2+3 (≤150ms)
    - "deep":     All 4 layers (≤300ms)

Verdict logic:
    - VERIFIED:        Watermark extracted with high confidence OR pHash match
    - LIKELY_VERIFIED:  Combined score > 0.8
    - SUSPICIOUS:       Combined score > 0.6
    - NOT_DETECTED:     Combined score ≤ 0.6
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
except ImportError:
    torch = None


# ──────────────────────────────────────────────────────────────────────
# Data Types
# ──────────────────────────────────────────────────────────────────────


class DetectionStatus(str, Enum):
    """Detection verdict categories."""
    VERIFIED = "VERIFIED"
    LIKELY_VERIFIED = "LIKELY_VERIFIED"
    SUSPICIOUS = "SUSPICIOUS"
    NOT_DETECTED = "NOT_DETECTED"


@dataclass
class LayerResult:
    """Result from a single detection layer."""
    name: str
    detected: bool
    confidence: float
    details: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


@dataclass
class DetectionResult:
    """Complete detection result from the ensemble."""
    status: DetectionStatus
    confidence: float
    layers: Dict[str, Dict[str, Any]]
    image_id: Optional[str]
    metadata: Dict[str, Any]
    total_latency_ms: float
    mode: str


# ──────────────────────────────────────────────────────────────────────
# Layer Weights
# ──────────────────────────────────────────────────────────────────────

DEFAULT_WEIGHTS = {
    "latent_watermark": 0.4,
    "perceptual_hash": 0.3,
    "cnn_classifier": 0.2,
    "vit_detector": 0.1,
}

# Thresholds for verdict
VERIFIED_THRESHOLD = 0.9       # Watermark confidence for instant VERIFIED
PHASH_THRESHOLD = 15           # Hamming distance for pHash match
COMBINED_VERIFIED_THRESHOLD = 0.8
COMBINED_SUSPICIOUS_THRESHOLD = 0.6


class TrinityDetector:
    """PROVENA Trinity 4-Layer Ensemble Detector.

    Orchestrates all detection layers and produces a unified verdict
    with weighted confidence scoring.

    Args:
        secret_key: 32-byte key for latent watermark detection.
        registry_service: Registry service for pHash lookup (optional).
        cnn_model_path: Path to trained CNN classifier weights.
        vit_model_path: Path to trained ViT detector weights.
        weights: Custom layer weights (default: 0.4/0.3/0.2/0.1).

    Example:
        >>> detector = TrinityDetector(secret_key=b"\\x00" * 32)
        >>> result = detector.detect(image, mode="deep")
        >>> print(f"{result.status}: {result.confidence:.2%}")
    """

    def __init__(
        self,
        secret_key: bytes,
        registry_service=None,
        cnn_model_path: Optional[str] = None,
        vit_model_path: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.secret_key = secret_key
        self.registry = registry_service
        self.weights = weights or DEFAULT_WEIGHTS

        # ── Layer 1: Latent Watermark Extractor ───────────────────
        try:
            from src.watermarking.latent.extractor import LatentWatermarkExtractor
            self.latent_extractor = LatentWatermarkExtractor(
                secret_key=secret_key, detection_threshold=0.5
            )
        except Exception as e:
            logger.warning(f"Latent extractor not available: {e}")
            self.latent_extractor = None

        # ── Layer 2: Perceptual Hash ──────────────────────────────
        try:
            from provena_flask.services.phash_service import PerceptualHashService
            self.phash_service = PerceptualHashService(threshold=PHASH_THRESHOLD)
        except Exception as e:
            logger.warning(f"pHash service not available: {e}")
            self.phash_service = None

        # ── Layer 3: CNN Classifier ───────────────────────────────
        try:
            from src.detection.cnn_classifier import CNNClassifier
            self.cnn_classifier = CNNClassifier(model_path=cnn_model_path)
        except Exception as e:
            logger.warning(f"CNN classifier not available: {e}")
            self.cnn_classifier = None

        # ── Layer 4: ViT Detector ─────────────────────────────────
        try:
            from src.detection.vit_detector import ViTDetector
            self.vit_detector = ViTDetector(model_path=vit_model_path)
        except Exception as e:
            logger.warning(f"ViT detector not available: {e}")
            self.vit_detector = None

        available = sum([
            self.latent_extractor is not None,
            self.phash_service is not None,
            self.cnn_classifier is not None,
            self.vit_detector is not None,
        ])
        logger.info(f"TrinityDetector initialised: {available}/4 layers available")

    def detect(
        self,
        image: np.ndarray,
        mode: str = "standard",
        candidate_ids: Optional[List[str]] = None,
    ) -> DetectionResult:
        """Run multi-layer detection on an image.

        Args:
            image: Input image (BGR numpy array, H×W×3).
            mode: Detection mode — "fast", "standard", or "deep".
            candidate_ids: Optional list of known image IDs for watermark matching.

        Returns:
            DetectionResult with status, confidence, per-layer results,
            detected image_id, and timing metadata.
        """
        t_start = time.time()
        layer_results: Dict[str, Dict[str, Any]] = {}
        detected_image_id = None

        # ── Layer 1: Latent Watermark ─────────────────────────────
        wm_confidence = 0.0
        if self.latent_extractor is not None:
            t0 = time.time()
            try:
                result, confidence = self.latent_extractor.extract(
                    image, candidate_ids=candidate_ids
                )
                wm_confidence = confidence
                if result is not None:
                    detected_image_id = result.get("image_id")
            except Exception as e:
                logger.warning(f"Watermark extraction error: {e}")
                result = None
                confidence = 0.0

            layer_results["latent_watermark"] = {
                "detected": result is not None,
                "confidence": wm_confidence,
                "image_id": detected_image_id,
                "latency_ms": (time.time() - t0) * 1000,
            }

            # Early exit: high-confidence watermark = VERIFIED
            if wm_confidence > VERIFIED_THRESHOLD:
                total_ms = (time.time() - t_start) * 1000
                return DetectionResult(
                    status=DetectionStatus.VERIFIED,
                    confidence=wm_confidence,
                    layers=layer_results,
                    image_id=detected_image_id,
                    metadata={"early_exit": "watermark_high_confidence"},
                    total_latency_ms=total_ms,
                    mode=mode,
                )

        # ── Layer 2: Perceptual Hash ──────────────────────────────
        phash_confidence = 0.0
        if self.phash_service is not None:
            t0 = time.time()
            try:
                phash = self.phash_service.compute_phash(image)

                # Search registry for matches
                matched = False
                distance = 64  # Max possible distance

                if self.registry is not None:
                    records = self.registry.search_by_perceptual_hash(f"phash:{phash}")
                    if records:
                        matched = True
                        detected_image_id = detected_image_id or records[0].get("image_id")
                        # Estimate distance (lower is better)
                        distance = 0

                # Confidence based on match
                phash_confidence = 1.0 if matched else 0.0

            except Exception as e:
                logger.warning(f"pHash error: {e}")
                matched = False
                distance = 64

            layer_results["perceptual_hash"] = {
                "matched": matched,
                "distance": distance,
                "hash": phash if "phash" in dir() else None,
                "confidence": phash_confidence,
                "latency_ms": (time.time() - t0) * 1000,
            }

            # Early exit: pHash match = VERIFIED
            if matched:
                total_ms = (time.time() - t_start) * 1000
                return DetectionResult(
                    status=DetectionStatus.VERIFIED,
                    confidence=0.95,
                    layers=layer_results,
                    image_id=detected_image_id,
                    metadata={"early_exit": "phash_match"},
                    total_latency_ms=total_ms,
                    mode=mode,
                )

        if mode == "fast":
            # Fast mode: only layers 1+2
            combined = (
                self.weights["latent_watermark"] * wm_confidence
                + self.weights["perceptual_hash"] * phash_confidence
            ) / (self.weights["latent_watermark"] + self.weights["perceptual_hash"])

            total_ms = (time.time() - t_start) * 1000
            return self._make_verdict(
                combined, layer_results, detected_image_id, total_ms, mode
            )

        # ── Layer 3: CNN Classifier ───────────────────────────────
        cnn_confidence = 0.0
        if self.cnn_classifier is not None:
            t0 = time.time()
            try:
                is_ai, cnn_confidence = self.cnn_classifier.predict(image)
            except Exception as e:
                logger.warning(f"CNN classifier error: {e}")
                is_ai = False
                cnn_confidence = 0.0

            layer_results["cnn_classifier"] = {
                "ai_generated": is_ai,
                "confidence": cnn_confidence,
                "latency_ms": (time.time() - t0) * 1000,
            }

        if mode == "standard":
            combined = (
                self.weights["latent_watermark"] * wm_confidence
                + self.weights["perceptual_hash"] * phash_confidence
                + self.weights["cnn_classifier"] * cnn_confidence
            ) / (
                self.weights["latent_watermark"]
                + self.weights["perceptual_hash"]
                + self.weights["cnn_classifier"]
            )

            total_ms = (time.time() - t_start) * 1000
            return self._make_verdict(
                combined, layer_results, detected_image_id, total_ms, mode
            )

        # ── Layer 4: ViT Detector ─────────────────────────────────
        vit_confidence = 0.0
        if self.vit_detector is not None:
            t0 = time.time()
            try:
                is_ai, vit_confidence = self.vit_detector.predict(image)
            except Exception as e:
                logger.warning(f"ViT detector error: {e}")
                is_ai = False
                vit_confidence = 0.0

            layer_results["vit_detector"] = {
                "ai_generated": is_ai,
                "confidence": vit_confidence,
                "latency_ms": (time.time() - t0) * 1000,
            }

        # ── Combined Score (all layers) ───────────────────────────
        combined = (
            self.weights["latent_watermark"] * wm_confidence
            + self.weights["perceptual_hash"] * phash_confidence
            + self.weights["cnn_classifier"] * cnn_confidence
            + self.weights["vit_detector"] * vit_confidence
        )

        total_ms = (time.time() - t_start) * 1000
        return self._make_verdict(
            combined, layer_results, detected_image_id, total_ms, mode
        )

    def _make_verdict(
        self,
        combined_score: float,
        layers: Dict,
        image_id: Optional[str],
        latency_ms: float,
        mode: str,
    ) -> DetectionResult:
        """Determine final verdict from combined score."""
        if combined_score >= COMBINED_VERIFIED_THRESHOLD:
            status = DetectionStatus.LIKELY_VERIFIED
        elif combined_score >= COMBINED_SUSPICIOUS_THRESHOLD:
            status = DetectionStatus.SUSPICIOUS
        else:
            status = DetectionStatus.NOT_DETECTED

        return DetectionResult(
            status=status,
            confidence=combined_score,
            layers=layers,
            image_id=image_id,
            metadata={},
            total_latency_ms=latency_ms,
            mode=mode,
        )

    def to_dict(self, result: DetectionResult) -> Dict[str, Any]:
        """Convert DetectionResult to JSON-serializable dictionary."""
        return {
            "status": result.status.value,
            "confidence": round(result.confidence, 4),
            "layers": result.layers,
            "image_id": result.image_id,
            "metadata": result.metadata,
            "total_latency_ms": round(result.total_latency_ms, 2),
            "mode": result.mode,
        }
