"""
benchmarks/latent_benchmark.py
================================
Comprehensive latent watermark robustness benchmark.

Tests detection rate under 10 transform families:
  JPEG, resize, screenshot, crop, colour jitter,
  Gaussian blur, salt-and-pepper noise, rotation, combined.

Usage:
    python benchmarks/latent_benchmark.py
    python benchmarks/latent_benchmark.py --n-images 100 --output results/latent.json

Output:
    - JSON results file
    - Markdown summary printed to stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple, Any

import cv2
import numpy as np

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Setup watermarking
# ─────────────────────────────────────────────────────────────────────────────

SECRET_KEY = bytes.fromhex("4a6f736570687175657a6f6e6e6561626c656b657968657831323334" + "0000")


def _make_image(size: int = 256, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (size, size, 3), dtype=np.uint8)


def _embed_latent(img: np.ndarray, image_id: str) -> np.ndarray:
    """Embed a latent watermark and return the watermarked image."""
    try:
        from src.watermarking.latent.pattern_generator import generate_pattern
        h, w = img.shape[:2]
        pattern = generate_pattern(image_id, SECRET_KEY, (h, w)).numpy()
        out = img.astype(np.float64) + pattern * 8.0
        return np.clip(out, 0, 255).astype(np.uint8)
    except Exception as e:
        print(f"[WARN] Embed failed: {e}")
        return img


def _detect_latent(img: np.ndarray, image_id: str) -> Tuple[bool, float]:
    """Run latent extraction. Returns (detected, confidence)."""
    try:
        from src.watermarking.latent.extractor import LatentWatermarkExtractor
        ext = LatentWatermarkExtractor(secret_key=SECRET_KEY, detection_threshold=0.5)
        result, confidence = ext.detect_only(img, image_id)
        return result, float(confidence)
    except Exception as e:
        return False, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Transform library
# ─────────────────────────────────────────────────────────────────────────────

def apply_jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    _, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                         [cv2.IMWRITE_JPEG_QUALITY, quality])
    decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)


def apply_resize(img: np.ndarray, scale: float) -> np.ndarray:
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    return cv2.resize(small, (w, h))


def apply_screenshot(img: np.ndarray) -> np.ndarray:
    """Simulate screenshot: Gaussian blur + slight chroma degradation."""
    blurred = cv2.GaussianBlur(img, (3, 3), 1.5)
    ycrcb = cv2.cvtColor(blurred, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    ycrcb[:, :, 1] += np.random.randn(*ycrcb[:, :, 1].shape) * 3
    ycrcb[:, :, 2] += np.random.randn(*ycrcb[:, :, 2].shape) * 3
    ycrcb = np.clip(ycrcb, 0, 255).astype(np.uint8)
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2RGB)


def apply_crop(img: np.ndarray, pct: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = int(min(h, w) * pct / 2)
    cropped = img[m:h - m, m:w - m]
    return cv2.resize(cropped, (w, h))


def apply_color_jitter(img: np.ndarray, strength: float = 0.2) -> np.ndarray:
    out = img.astype(np.float32)
    out *= (1.0 + np.random.uniform(-strength, strength))
    out += np.random.uniform(-strength * 30, strength * 30)
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    ksize = max(3, int(sigma * 4) | 1)   # odd kernel
    return cv2.GaussianBlur(img, (ksize, ksize), sigma)


def apply_salt_pepper(img: np.ndarray, pct: float) -> np.ndarray:
    out = img.copy()
    rng = np.random.default_rng()
    n = int(img.shape[0] * img.shape[1] * pct)
    ys = rng.integers(0, img.shape[0], n)
    xs = rng.integers(0, img.shape[1], n)
    out[ys[:n//2], xs[:n//2]] = 255
    out[ys[n//2:], xs[n//2:]] = 0
    return out


def apply_rotation(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h))
    # Crop border artifacts
    crop = int(min(h, w) * abs(np.sin(np.radians(angle))) / 2)
    if crop > 0 and 2 * crop < min(h, w):
        rotated = rotated[crop:h - crop, crop:w - crop]
        rotated = cv2.resize(rotated, (w, h))
    return rotated


TRANSFORMS = {
    "clean":              lambda img: img.copy(),
    "jpeg_q10":           lambda img: apply_jpeg(img, 10),
    "jpeg_q30":           lambda img: apply_jpeg(img, 30),
    "jpeg_q50":           lambda img: apply_jpeg(img, 50),
    "jpeg_q75":           lambda img: apply_jpeg(img, 75),
    "jpeg_q90":           lambda img: apply_jpeg(img, 90),
    "resize_025x":        lambda img: apply_resize(img, 0.25),
    "resize_050x":        lambda img: apply_resize(img, 0.50),
    "resize_075x":        lambda img: apply_resize(img, 0.75),
    "resize_150x":        lambda img: apply_resize(img, 1.50),
    "resize_200x":        lambda img: apply_resize(img, 2.00),
    "screenshot":         lambda img: apply_screenshot(img),
    "crop_10pct":         lambda img: apply_crop(img, 0.10),
    "crop_20pct":         lambda img: apply_crop(img, 0.20),
    "crop_30pct":         lambda img: apply_crop(img, 0.30),
    "crop_40pct":         lambda img: apply_crop(img, 0.40),
    "crop_50pct":         lambda img: apply_crop(img, 0.50),
    "color_jitter_20pct": lambda img: apply_color_jitter(img, 0.20),
    "gaussian_blur_0.5":  lambda img: apply_gaussian_blur(img, 0.5),
    "gaussian_blur_1.0":  lambda img: apply_gaussian_blur(img, 1.0),
    "gaussian_blur_1.5":  lambda img: apply_gaussian_blur(img, 1.5),
    "gaussian_blur_2.0":  lambda img: apply_gaussian_blur(img, 2.0),
    "salt_pepper_1pct":   lambda img: apply_salt_pepper(img, 0.01),
    "salt_pepper_2pct":   lambda img: apply_salt_pepper(img, 0.02),
    "salt_pepper_5pct":   lambda img: apply_salt_pepper(img, 0.05),
    "rotation_5deg":      lambda img: apply_rotation(img, 5),
    "rotation_10deg":     lambda img: apply_rotation(img, 10),
    "rotation_15deg":     lambda img: apply_rotation(img, 15),
    "combined":           lambda img: apply_crop(apply_resize(apply_jpeg(img, 50), 0.5), 0.20),
}


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark runner
# ─────────────────────────────────────────────────────────────────────────────

def run_benchmark(n_images: int = 50) -> Dict[str, Any]:
    """Run the full latent watermark benchmark."""
    results: Dict[str, List[bool]] = {name: [] for name in TRANSFORMS}
    latencies: Dict[str, List[float]] = {name: [] for name in TRANSFORMS}

    print(f"\n📊 Latent Watermark Robustness Benchmark ({n_images} images × {len(TRANSFORMS)} transforms)")
    print("=" * 70)

    for idx in range(n_images):
        image_id = f"bench-{idx:05d}"
        orig = _make_image(256, seed=idx)
        wm_img = _embed_latent(orig, image_id)

        for t_name, t_fn in TRANSFORMS.items():
            try:
                t_img = t_fn(wm_img)
            except Exception as e:
                results[t_name].append(False)
                latencies[t_name].append(0.0)
                continue

            t0 = time.perf_counter()
            detected, conf = _detect_latent(t_img, image_id)
            elapsed = (time.perf_counter() - t0) * 1000

            results[t_name].append(detected)
            latencies[t_name].append(elapsed)

        if (idx + 1) % 10 == 0:
            print(f"  Progress: {idx + 1}/{n_images} images processed…")

    # Aggregate
    summary = {}
    for name in TRANSFORMS:
        detections = results[name]
        lats = latencies[name]
        summary[name] = {
            "detection_rate":     sum(detections) / len(detections) if detections else 0.0,
            "detected":           sum(detections),
            "total":              len(detections),
            "avg_latency_ms":     float(np.mean(lats)) if lats else 0.0,
            "p95_latency_ms":     float(np.percentile(lats, 95)) if lats else 0.0,
        }

    return {
        "benchmark":   "latent_watermark_robustness",
        "n_images":    n_images,
        "n_transforms": len(TRANSFORMS),
        "results":     summary,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def print_markdown_report(data: Dict[str, Any]) -> None:
    print(f"\n## Latent Watermark Robustness Report")
    print(f"**Images tested:** {data['n_images']}  |  **Transforms:** {data['n_transforms']}\n")
    print(f"| Transform | Detection Rate | Avg Latency (ms) |")
    print(f"|-----------|:--------------:|:----------------:|")
    for name, stats in data["results"].items():
        rate_pct = stats["detection_rate"] * 100
        emoji = "✅" if rate_pct >= 90 else ("⚠️" if rate_pct >= 70 else "❌")
        print(f"| {name:<25} | {emoji} {rate_pct:5.1f}% | {stats['avg_latency_ms']:6.1f} |")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Latent watermark robustness benchmark")
    parser.add_argument("--n-images", type=int, default=50, help="Number of test images")
    parser.add_argument("--output",   type=str, default="results/latent_benchmark.json")
    args = parser.parse_args()

    data = run_benchmark(args.n_images)
    print_markdown_report(data)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\n✅ Results saved to {out_path}")
