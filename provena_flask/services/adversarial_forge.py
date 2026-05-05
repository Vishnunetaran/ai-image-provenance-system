"""
AdversarialForge — Automated robustness testing engine.

Attacks watermarked images with a catalog of transforms and measures
which watermark layers survive each attack. Returns a RobustnessReport
with per-attack, per-layer survival data.

This is Provena's built-in red team. Every registration can optionally
include a robustness assessment that proves the system is honest about
its limitations.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)


@dataclass
class AttackResult:
    """Result of a single attack on a watermarked image."""
    attack_name: str
    survived: bool                    # Did at least one layer decode correctly?
    layers_survived: int              # How many layers decoded correctly
    layers_total: int                 # Total layers attempted
    time_ms: int                      # Time taken for this attack test
    details: dict = field(default_factory=dict)


@dataclass
class RobustnessReport:
    """Complete robustness report for a watermarked image."""
    total_attacks: int
    attacks_survived: int
    survival_rate: float              # 0.0–1.0
    results: list[AttackResult]
    total_time_ms: int


# ---------------------------------------------------------------------------
# Attack transforms
# ---------------------------------------------------------------------------

def _attack_jpeg(image: Image.Image, quality: int) -> Image.Image:
    """JPEG compress and decompress at given quality."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _attack_resize(image: Image.Image, factor: float) -> Image.Image:
    """Resize image by factor then back to original size."""
    w, h = image.size
    small = image.resize((int(w * factor), int(h * factor)), Image.BILINEAR)
    return small.resize((w, h), Image.BILINEAR)


def _attack_crop_center(image: Image.Image, fraction: float) -> Image.Image:
    """Crop a centered fraction of the image, then resize back to original."""
    w, h = image.size
    cw, ch = int(w * (1 - fraction)), int(h * (1 - fraction))
    left = (w - cw) // 2
    top = (h - ch) // 2
    cropped = image.crop((left, top, left + cw, top + ch))
    return cropped.resize((w, h), Image.BILINEAR)


def _attack_blur(image: Image.Image, radius: float) -> Image.Image:
    """Apply Gaussian blur."""
    return image.filter(ImageFilter.GaussianBlur(radius=radius))


def _attack_noise(image: Image.Image, sigma: float) -> Image.Image:
    """Add Gaussian noise."""
    arr = np.array(image, dtype=np.float64)
    noise = np.random.normal(0, sigma, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _attack_color_shift(image: Image.Image, shift: int) -> Image.Image:
    """Shift all pixel values by a fixed amount."""
    arr = np.array(image, dtype=np.int16)
    arr = np.clip(arr + shift, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _attack_sharpen(image: Image.Image) -> Image.Image:
    """Apply sharpening filter."""
    return image.filter(ImageFilter.SHARPEN)


def _attack_rotate(image: Image.Image, degrees: float) -> Image.Image:
    """Rotate image slightly and crop back."""
    rotated = image.rotate(degrees, resample=Image.BILINEAR, expand=False)
    return rotated


# ---------------------------------------------------------------------------
# Attack catalog
# ---------------------------------------------------------------------------

ATTACK_CATALOG = [
    ("jpeg_q50",        lambda img: _attack_jpeg(img, 50)),
    ("jpeg_q75",        lambda img: _attack_jpeg(img, 75)),
    ("jpeg_q90",        lambda img: _attack_jpeg(img, 90)),
    ("resize_0.5x",     lambda img: _attack_resize(img, 0.5)),
    ("resize_2.0x",     lambda img: _attack_resize(img, 2.0)),
    ("crop_25pct",      lambda img: _attack_crop_center(img, 0.25)),
    ("crop_50pct",      lambda img: _attack_crop_center(img, 0.50)),
    ("blur_sigma2",     lambda img: _attack_blur(img, 2.0)),
    ("noise_sigma10",   lambda img: _attack_noise(img, 10.0)),
    ("color_shift_+15", lambda img: _attack_color_shift(img, 15)),
    ("sharpen",         lambda img: _attack_sharpen(img)),
    ("rotate_5deg",     lambda img: _attack_rotate(img, 5.0)),
]


def test_robustness(
    watermarked_image: Image.Image,
    expected_payload: bytes,
    attacks: Optional[list] = None,
) -> RobustnessReport:
    """
    Run the full attack catalog against a watermarked image.

    For each attack:
    1. Apply the transform to produce a degraded image
    2. Run HydraWatermark.extract() on the degraded image
    3. Check if the extracted payload matches the expected payload
    4. Record per-layer survival details

    Args:
        watermarked_image: The watermarked PIL Image to test.
        expected_payload:  The 6-byte payload that should be extracted.
        attacks:           Optional subset of attacks. Default: full catalog.

    Returns:
        RobustnessReport with per-attack results and overall survival rate.
    """
    from provena_flask.services.hydra_watermark import extract

    if attacks is None:
        attacks = ATTACK_CATALOG

    results = []
    t0 = time.time()

    for attack_name, attack_fn in attacks:
        t_attack = time.time()

        try:
            # Apply the attack transform
            attacked_image = attack_fn(watermarked_image.copy())

            # Attempt extraction
            vote_result = extract(attacked_image, min_confidence=0.3)

            # Check if the winning payload matches
            survived = (vote_result.payload == expected_payload and vote_result.confidence > 0)

            # Count per-layer survival
            layers_survived = 0
            layer_details = {}
            for layer_name, info in vote_result.breakdown.items():
                layer_payload_hex = info.get("payload_hex", "empty")
                expected_hex = expected_payload.hex()
                layer_ok = (layer_payload_hex == expected_hex and info.get("confidence", 0) >= 0.3)
                if layer_ok:
                    layers_survived += 1
                layer_details[layer_name] = {
                    "survived": layer_ok,
                    "confidence": info.get("confidence", 0.0),
                    "payload_hex": layer_payload_hex,
                }

            elapsed = round((time.time() - t_attack) * 1000)

            results.append(AttackResult(
                attack_name=attack_name,
                survived=survived,
                layers_survived=layers_survived,
                layers_total=len(vote_result.breakdown),
                time_ms=elapsed,
                details=layer_details,
            ))

            status = "SURVIVED" if survived else "FAILED"
            logger.info(
                "AdversarialForge: %s -> %s (%d/%d layers, %d ms)",
                attack_name, status, layers_survived, len(vote_result.breakdown), elapsed,
            )

        except Exception as exc:
            elapsed = round((time.time() - t_attack) * 1000)
            results.append(AttackResult(
                attack_name=attack_name,
                survived=False,
                layers_survived=0,
                layers_total=3,
                time_ms=elapsed,
                details={"error": str(exc)[:200]},
            ))
            logger.warning("AdversarialForge: %s -> ERROR: %s", attack_name, exc)

    total_ms = round((time.time() - t0) * 1000)
    attacks_survived = sum(1 for r in results if r.survived)
    survival_rate = attacks_survived / len(results) if results else 0.0

    report = RobustnessReport(
        total_attacks=len(results),
        attacks_survived=attacks_survived,
        survival_rate=round(survival_rate, 4),
        results=results,
        total_time_ms=total_ms,
    )

    logger.info(
        "AdversarialForge complete: %d/%d attacks survived (%.0f%%), %d ms total",
        attacks_survived, len(results), survival_rate * 100, total_ms,
    )

    return report


def report_to_dict(report: RobustnessReport) -> dict:
    """Convert a RobustnessReport to a JSON-serializable dict."""
    return {
        "total_attacks": report.total_attacks,
        "attacks_survived": report.attacks_survived,
        "survival_rate": report.survival_rate,
        "total_time_ms": report.total_time_ms,
        "attacks": [
            {
                "name": r.attack_name,
                "survived": r.survived,
                "layers_survived": r.layers_survived,
                "layers_total": r.layers_total,
                "time_ms": r.time_ms,
                "details": r.details,
            }
            for r in report.results
        ],
    }
