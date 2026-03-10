"""
Latent Watermark Benchmark Runner.

Generates a configurable number of test images, applies all transform
combinations, measures detection rates, and outputs structured JSON results
suitable for report generation.

Usage:
    python benchmarks/latent_watermark_benchmark.py --num-images 50
    python benchmarks/latent_watermark_benchmark.py --num-images 100 --output results.json
"""

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from typing import Dict, List

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import torch
    import cv2
except ImportError as e:
    print(f"Missing dependency: {e}. Install torch and opencv-python.")
    sys.exit(1)

from src.watermarking.latent.pattern_generator import generate_pattern
from src.watermarking.latent.extractor import LatentWatermarkExtractor

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────

SECRET_KEY = b"\x42" * 32
IMAGE_SIZE = 256
EMBED_STRENGTH = 40.0

TRANSFORMS = {
    "clean": {"fn": lambda img: img, "params": [None]},
    "jpeg": {
        "fn": lambda img, q: _jpeg_compress(img, q),
        "params": [90, 75, 50, 30, 10],
        "label": "Q",
    },
    "resize": {
        "fn": lambda img, s: _resize(img, s),
        "params": [2.0, 1.5, 0.75, 0.5, 0.25],
        "label": "scale",
    },
    "crop": {
        "fn": lambda img, p: _crop(img, p),
        "params": [0.10, 0.20, 0.30],
        "label": "pct",
    },
    "blur": {
        "fn": lambda img, s: _blur(img, s),
        "params": [0.5, 1.0, 1.5, 2.0],
        "label": "sigma",
    },
    "brightness": {
        "fn": lambda img, f: _brightness(img, f),
        "params": [0.7, 0.85, 1.15, 1.3],
        "label": "factor",
    },
    "contrast": {
        "fn": lambda img, f: _contrast(img, f),
        "params": [0.8, 0.9, 1.1, 1.2],
        "label": "factor",
    },
}


# ──────────────────────────────────────────────────────────────────────
# Transform Functions
# ──────────────────────────────────────────────────────────────────────


def _jpeg_compress(image, quality):
    tmp = tempfile.mktemp(suffix=".jpg")
    try:
        cv2.imwrite(tmp, image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return cv2.imread(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _resize(image, scale):
    h, w = image.shape[:2]
    small = cv2.resize(image, (int(w * scale), int(h * scale)))
    return cv2.resize(small, (w, h))


def _crop(image, pct):
    h, w = image.shape[:2]
    mh, mw = int(h * pct / 2), int(w * pct / 2)
    return cv2.resize(image[mh : h - mh, mw : w - mw], (w, h))


def _blur(image, sigma):
    ksize = int(sigma * 6) | 1
    return cv2.GaussianBlur(image, (ksize, ksize), sigma)


def _brightness(image, factor):
    return np.clip(image.astype(np.float64) * factor, 0, 255).astype(np.uint8)


def _contrast(image, factor):
    return np.clip(127.5 + (image.astype(np.float64) - 127.5) * factor, 0, 255).astype(
        np.uint8
    )


# ──────────────────────────────────────────────────────────────────────
# Benchmark Logic
# ──────────────────────────────────────────────────────────────────────


def create_watermarked_image(image_id: str) -> np.ndarray:
    """Create synthetic watermarked image."""
    base = np.zeros((IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.float64)
    for i in range(IMAGE_SIZE):
        for j in range(IMAGE_SIZE):
            base[i, j, 0] = 128 + 60 * np.sin(i / 15.0 + hash(image_id) % 10)
            base[i, j, 1] = 128 + 60 * np.cos(j / 15.0 + hash(image_id) % 7)
            base[i, j, 2] = 128 + 40 * np.sin((i + j) / 20.0)

    pattern = generate_pattern(
        image_id, SECRET_KEY, (IMAGE_SIZE, IMAGE_SIZE), apply_freq_shaping=False
    ).numpy()
    for c in range(3):
        base[:, :, c] += pattern * EMBED_STRENGTH
    return np.clip(base, 0, 255).astype(np.uint8)


def run_benchmark(num_images: int) -> Dict:
    """Run the full benchmark suite.

    Args:
        num_images: Number of synthetic images to test.

    Returns:
        Dictionary of results keyed by transform name.
    """
    extractor = LatentWatermarkExtractor(
        secret_key=SECRET_KEY, detection_threshold=0.5
    )

    results: Dict[str, Dict] = {}
    total_start = time.time()

    for transform_name, spec in TRANSFORMS.items():
        fn = spec["fn"]
        params_list = spec["params"]
        label = spec.get("label", "")

        for param in params_list:
            param_label = f"{transform_name}_{label}{param}" if param is not None else transform_name
            print(f"  Testing: {param_label} ...", end=" ", flush=True)

            detections = 0
            confidences: List[float] = []
            times_ms: List[float] = []

            for idx in range(num_images):
                image_id = f"bench-{idx:04d}"
                image = create_watermarked_image(image_id)

                # Apply transform
                if param is not None:
                    transformed = fn(image, param)
                else:
                    transformed = fn(image)

                # Detect
                t0 = time.time()
                detected, confidence = extractor.detect_only(transformed, image_id)
                elapsed_ms = (time.time() - t0) * 1000

                if detected:
                    detections += 1
                confidences.append(confidence)
                times_ms.append(elapsed_ms)

            detection_rate = detections / num_images
            avg_conf = np.mean(confidences)
            avg_time = np.mean(times_ms)

            results[param_label] = {
                "transform": transform_name,
                "param": param,
                "detection_rate": round(detection_rate, 4),
                "avg_confidence": round(float(avg_conf), 4),
                "min_confidence": round(float(np.min(confidences)), 4),
                "max_confidence": round(float(np.max(confidences)), 4),
                "avg_latency_ms": round(float(avg_time), 2),
                "num_images": num_images,
            }

            print(
                f"rate={detection_rate:.1%}, avg_conf={avg_conf:.3f}, "
                f"time={avg_time:.1f}ms"
            )

    total_time = time.time() - total_start
    return {
        "results": results,
        "metadata": {
            "num_images": num_images,
            "image_size": IMAGE_SIZE,
            "embed_strength": EMBED_STRENGTH,
            "total_time_seconds": round(total_time, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Latent watermark benchmark")
    parser.add_argument("--num-images", type=int, default=20, help="Number of test images")
    parser.add_argument("--output", type=str, default="benchmarks/benchmark_results.json")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  PROVENA Trinity – Latent Watermark Benchmark")
    print(f"  Images: {args.num_images} | Size: {IMAGE_SIZE}x{IMAGE_SIZE}")
    print(f"{'='*60}\n")

    results = run_benchmark(args.num_images)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Results saved to {args.output}")


if __name__ == "__main__":
    main()
