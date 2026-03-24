from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import requests
from PIL import Image, ImageFilter, ImageEnhance
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESULTS_DIR = Path("benchmark/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

# Minimum acceptable thresholds (from PRD)
THRESHOLDS = {
    "clean":             0.95,   # no transform applied
    "jpeg_q70":          0.95,   # JPEG compression quality 70
    "jpeg_q50":          0.90,   # JPEG compression quality 50
    "jpeg_q30":          0.75,   # JPEG compression quality 30 (stretch goal)
    "crop_10pct":        0.88,   # 10% random crop
    "resize_512":        0.85,   # resize to 512px (longest edge)
    "gaussian_blur":     0.85,   # Gaussian blur sigma=1
    "brightness":        0.85,   # brightness ±20%
    "social_media_sim":  0.70,   # chained: JPEG q=75 + resize + slight noise
    "screenshot_sim":    0.70,   # chained: render at 2x, capture, JPEG q=80
}

# ---------------------------------------------------------------------------
# Attack functions — each takes a PIL.Image, returns a PIL.Image
# ---------------------------------------------------------------------------

def attack_clean(img: Image.Image) -> Image.Image:
    return img.copy()

def attack_jpeg(quality: int) -> Callable[[Image.Image], Image.Image]:
    def _attack(img: Image.Image) -> Image.Image:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        buf.seek(0)
        return Image.open(buf).copy()
    return _attack

def attack_crop(pct: float) -> Callable[[Image.Image], Image.Image]:
    def _attack(img: Image.Image) -> Image.Image:
        w, h = img.size
        cx = int(w * pct / 2)
        cy = int(h * pct / 2)
        return img.crop((cx, cy, w - cx, h - cy))
    return _attack

def attack_resize(target_px: int) -> Callable[[Image.Image], Image.Image]:
    def _attack(img: Image.Image) -> Image.Image:
        w, h = img.size
        scale = target_px / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        return img.resize((new_w, new_h), Image.LANCZOS)
    return _attack

def attack_gaussian_blur(sigma: float = 1.0) -> Callable[[Image.Image], Image.Image]:
    def _attack(img: Image.Image) -> Image.Image:
        return img.filter(ImageFilter.GaussianBlur(radius=sigma))
    return _attack

def attack_brightness(factor: float = 1.2) -> Callable[[Image.Image], Image.Image]:
    def _attack(img: Image.Image) -> Image.Image:
        return ImageEnhance.Brightness(img).enhance(factor)
    return _attack

def attack_social_media_sim(img: Image.Image) -> Image.Image:
    """Simulate a typical social media upload pipeline."""
    img = attack_resize(1080)(img)
    img = attack_jpeg(75)(img)
    arr = np.array(img).astype(np.float32)
    noise = np.random.normal(0, 2, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    img = attack_jpeg(80)(img)
    return img

def attack_screenshot_sim(img: Image.Image) -> Image.Image:
    """Simulate screenshot: upscale (screen render), downscale (capture), JPEG."""
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = img.resize((w, h), Image.LANCZOS)
    img = attack_jpeg(80)(img)
    return img

ATTACKS: dict[str, Callable[[Image.Image], Image.Image]] = {
    "clean":           attack_clean,
    "jpeg_q70":        attack_jpeg(70),
    "jpeg_q50":        attack_jpeg(50),
    "jpeg_q30":        attack_jpeg(30),
    "crop_10pct":      attack_crop(0.10),
    "resize_512":      attack_resize(512),
    "gaussian_blur":   attack_gaussian_blur(1.0),
    "brightness":      attack_brightness(1.2),
    "social_media_sim": attack_social_media_sim,
    "screenshot_sim":  attack_screenshot_sim,
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AttackResult:
    attack_name: str
    total: int = 0
    verified: int = 0           # status == VERIFIED
    verified_modified: int = 0  # status == VERIFIED_MODIFIED
    unregistered: int = 0       # status == UNREGISTERED (false negative)
    tampered: int = 0           # status == TAMPERED (unexpected)
    errors: int = 0             # API errors / timeouts
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        detected = self.verified + self.verified_modified
        base = self.total - self.errors
        return detected / base if base > 0 else 0.0

    @property
    def p50_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return float(np.percentile(self.latencies_ms, 50))

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return float(np.percentile(self.latencies_ms, 99))

    @property
    def passes(self) -> bool:
        threshold = THRESHOLDS.get(self.attack_name, 0.80)
        return self.detection_rate >= threshold

@dataclass
class CorrectnessResult:
    """Validate that unregistered images return UNREGISTERED (false positive rate)."""
    total: int = 0
    false_positives: int = 0   # returned VERIFIED/VERIFIED_MODIFIED for an unregistered image

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.total if self.total > 0 else 0.0

    @property
    def passes(self) -> bool:
        return self.false_positive_rate < 0.001  # PRD target: <0.1%

@dataclass
class LatencyResult:
    """End-to-end API latency under load."""
    register_p99_ms: float = 0.0
    verify_p99_ms: float = 0.0

    @property
    def passes(self) -> bool:
        return self.register_p99_ms < 2000 and self.verify_p99_ms < 500

# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class ProvenClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
        })
        self.timeout = timeout

    def register(self, image: Image.Image, model_id: str = "benchmark-model") -> dict:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        start = time.perf_counter()
        resp = self.session.post(
            f"{self.base_url}/api/v1/register",
            files={"image": ("image.png", buf, "image/png")},
            data={"model_id": model_id, "creator_did": "did:key:benchmark"},
            timeout=self.timeout,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        result = resp.json()
        result["_latency_ms"] = elapsed_ms
        return result

    def verify(self, image: Image.Image) -> dict:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        start = time.perf_counter()
        resp = self.session.post(
            f"{self.base_url}/api/v1/verify",
            files={"image": ("image.png", buf, "image/png")},
            timeout=self.timeout,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        result = resp.json()
        result["_latency_ms"] = elapsed_ms
        return result

# ---------------------------------------------------------------------------
# Image loader
# ---------------------------------------------------------------------------

def load_images(images_dir: Path | None, n: int = 50) -> list[Image.Image]:
    """
    Load test images. If a directory is provided, load from it.
    Otherwise, synthesise n solid-color images as a fallback
    (smoke test only — results won't be representative).
    """
    images: list[Image.Image] = []

    if images_dir and images_dir.exists():
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        paths = [p for p in images_dir.iterdir() if p.suffix.lower() in exts][:n]
        for p in paths:
            try:
                images.append(Image.open(p).convert("RGB"))
            except Exception:
                pass
        if images:
            print(f"  Loaded {len(images)} images from {images_dir}")
            return images

    # Fallback: synthetic images
    print("  WARNING: No test images provided. Using synthetic images for smoke test only.")
    print("  Results will NOT be representative. Provide real AI-generated images via --images.")
    rng = np.random.default_rng(42)
    for _ in range(n):
        arr = rng.integers(0, 255, (512, 512, 3), dtype=np.uint8)
        images.append(Image.fromarray(arr))
    return images

# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------

def run_robustness_benchmark(
    client: ProvenClient,
    images: list[Image.Image],
) -> dict[str, AttackResult]:
    print("\n[1/3] Robustness benchmark")
    print(f"  {len(images)} images × {len(ATTACKS)} attacks = {len(images) * len(ATTACKS)} verify calls\n")

    results: dict[str, AttackResult] = {name: AttackResult(attack_name=name) for name in ATTACKS}

    for i, img in enumerate(tqdm(images, desc="  Images", unit="img")):
        # Register the watermarked image first
        try:
            reg = client.register(img)
        except Exception as e:
            print(f"\n  SKIP image {i}: register failed — {e}")
            for r in results.values():
                r.errors += 1
                r.total += 1
            continue

        # Decode the watermarked image returned by the API
        try:
            wm_b64 = reg.get("watermarked_image", "")
            if wm_b64:
                import base64
                wm_bytes = base64.b64decode(wm_b64)
                wm_img = Image.open(io.BytesIO(wm_bytes)).convert("RGB")
            else:
                wm_img = img  # fallback if API doesn't return watermarked image
        except Exception:
            wm_img = img

        # Apply each attack and verify
        for attack_name, attack_fn in ATTACKS.items():
            result = results[attack_name]
            result.total += 1
            try:
                attacked = attack_fn(wm_img)
                verify = client.verify(attacked)
                status = verify.get("status", "UNKNOWN")
                result.latencies_ms.append(verify["_latency_ms"])

                if status == "VERIFIED":
                    result.verified += 1
                elif status == "VERIFIED_MODIFIED":
                    result.verified_modified += 1
                elif status == "UNREGISTERED":
                    result.unregistered += 1
                elif status == "TAMPERED":
                    result.tampered += 1

            except Exception as e:
                result.errors += 1

    return results


def run_false_positive_benchmark(
    client: ProvenClient,
    n: int = 200,
) -> CorrectnessResult:
    """Send images that were NEVER registered. All should return UNREGISTERED."""
    print("\n[2/3] False positive benchmark")
    print(f"  {n} unregistered synthetic images\n")

    result = CorrectnessResult(total=n)
    rng = np.random.default_rng(99)

    for _ in tqdm(range(n), desc="  Unregistered images", unit="img"):
        arr = rng.integers(0, 255, (512, 512, 3), dtype=np.uint8)
        img = Image.fromarray(arr)
        try:
            verify = client.verify(img)
            status = verify.get("status", "UNKNOWN")
            if status in ("VERIFIED", "VERIFIED_MODIFIED"):
                result.false_positives += 1
        except Exception:
            pass

    return result


def run_latency_benchmark(
    client: ProvenClient,
    n: int = 30,
) -> LatencyResult:
    """Measure raw API latency for register and verify with a clean image."""
    print("\n[3/3] Latency benchmark")
    print(f"  {n} register calls + {n} verify calls\n")

    register_latencies: list[float] = []
    verify_latencies: list[float] = []
    rng = np.random.default_rng(7)
    img = Image.fromarray(rng.integers(0, 255, (512, 512, 3), dtype=np.uint8))

    for _ in tqdm(range(n), desc="  Register", unit="req"):
        try:
            reg = client.register(img)
            register_latencies.append(reg["_latency_ms"])
        except Exception:
            pass

    for _ in tqdm(range(n), desc="  Verify  ", unit="req"):
        try:
            v = client.verify(img)
            verify_latencies.append(v["_latency_ms"])
        except Exception:
            pass

    return LatencyResult(
        register_p99_ms=float(np.percentile(register_latencies, 99)) if register_latencies else 9999,
        verify_p99_ms=float(np.percentile(verify_latencies, 99)) if verify_latencies else 9999,
    )

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"

def _pf(ok: bool) -> str:
    return PASS if ok else FAIL

def build_text_report(
    robustness: dict[str, AttackResult],
    correctness: CorrectnessResult,
    latency: LatencyResult,
) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append("PROVENA v2 — BENCHMARK REPORT")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append("=" * 64)

    lines.append("\n── ROBUSTNESS ─────────────────────────────────────────────")
    lines.append(f"{'Attack':<22} {'Detected':>8} {'Rate':>7} {'Threshold':>10} {'P99ms':>7} {'Result':>7}")
    lines.append("-" * 64)

    overall_pass = True
    for name, r in robustness.items():
        threshold = THRESHOLDS.get(name, 0.80)
        rate_str = f"{r.detection_rate * 100:.1f}%"
        thr_str = f"{threshold * 100:.0f}%"
        detected = r.verified + r.verified_modified
        pf = _pf(r.passes)
        if not r.passes:
            overall_pass = False
        lines.append(
            f"{name:<22} {detected:>4}/{r.total - r.errors:<3} {rate_str:>7} {thr_str:>10} "
            f"{r.p99_ms:>6.0f} {pf:>7}"
        )

    lines.append("\n── CORRECTNESS (false positives) ───────────────────────────")
    lines.append(f"  Unregistered images tested : {correctness.total}")
    lines.append(f"  False positives            : {correctness.false_positives}")
    lines.append(f"  False positive rate        : {correctness.false_positive_rate * 100:.3f}%")
    lines.append(f"  Threshold                  : <0.100%")
    lines.append(f"  Result                     : {_pf(correctness.passes)}")
    if not correctness.passes:
        overall_pass = False

    lines.append("\n── LATENCY ─────────────────────────────────────────────────")
    lines.append(f"  Register P99               : {latency.register_p99_ms:.0f}ms  (threshold: <2000ms)  {_pf(latency.register_p99_ms < 2000)}")
    lines.append(f"  Verify   P99               : {latency.verify_p99_ms:.0f}ms  (threshold:  <500ms)  {_pf(latency.verify_p99_ms < 500)}")
    if not latency.passes:
        overall_pass = False

    lines.append("\n" + "=" * 64)
    lines.append(f"OVERALL: {'ALL CHECKS PASSED' if overall_pass else 'ONE OR MORE CHECKS FAILED'}")
    lines.append("=" * 64)

    return "\n".join(lines)


def build_html_report(
    robustness: dict[str, AttackResult],
    correctness: CorrectnessResult,
    latency: LatencyResult,
    text_report: str,
) -> str:
    rows = ""
    for name, r in robustness.items():
        threshold = THRESHOLDS.get(name, 0.80)
        rate_pct = r.detection_rate * 100
        color = "#1D9E75" if r.passes else "#E24B4A"
        bar_w = int(rate_pct)
        thr_bar = int(threshold * 100)
        rows += f"""
        <tr>
          <td style="padding:8px 12px;font-size:13px">{name}</td>
          <td style="padding:8px 12px;font-size:13px">{r.verified + r.verified_modified}/{r.total - r.errors}</td>
          <td style="padding:8px 12px;min-width:180px">
            <div style="background:#f0ede6;border-radius:4px;height:14px;position:relative">
              <div style="background:{color};width:{bar_w}%;height:100%;border-radius:4px"></div>
              <div style="position:absolute;top:0;left:{thr_bar}%;width:2px;height:100%;background:#444;opacity:0.4"></div>
            </div>
            <span style="font-size:11px;color:#666">{rate_pct:.1f}% (need {threshold*100:.0f}%)</span>
          </td>
          <td style="padding:8px 12px;font-size:13px">{r.p99_ms:.0f}ms</td>
          <td style="padding:8px 12px">
            <span style="background:{'#e1f5ee' if r.passes else '#fcebeb'};color:{color};
                         padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500">
              {'PASS' if r.passes else 'FAIL'}
            </span>
          </td>
        </tr>"""

    overall_ok = all(r.passes for r in robustness.values()) and correctness.passes and latency.passes

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Provena v2 Benchmark Report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 0; padding: 32px; background: #fafaf8; color: #1a1a18; }}
  h1 {{ font-size: 22px; font-weight: 500; margin-bottom: 4px }}
  table {{ width: 100%; border-collapse: collapse; background: white; border: 0.5px solid #e0ddd4 }}
  th {{ text-align: left; padding: 10px 12px; font-size: 12px; color: #888; background: #fafaf8 }}
  pre {{ background: #1e1e1e; color: #d4d4d4; padding: 20px; border-radius: 10px; font-size: 12px }}
</style>
</head>
<body>
<h1>Provena v2 — Benchmark Report</h1>
<div class="overall">{'ALL CHECKS PASSED' if overall_ok else 'ONE OR MORE CHECKS FAILED'}</div>
<table>
  <thead><tr><th>Attack</th><th>Detected</th><th>Rate vs threshold</th><th>Verify P99</th><th>Result</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<h2>Full text report</h2>
<pre>{text_report}</pre>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Provena v2 robustness benchmark")
    parser.add_argument("--api-url", default="http://localhost:5000", help="Base URL of your Provena API")
    parser.add_argument("--api-key", required=True, help="API key (prov_sk_...)")
    parser.add_argument("--n", type=int, default=10, help="Number of images to test")
    parser.add_argument("--fp-n", type=int, default=20, help="False positive test count")
    parser.add_argument("--latency-n", type=int, default=10, help="Latency test count")
    args = parser.parse_args()

    print("=" * 64)
    print("PROVENA v2 BENCHMARK SUITE")
    print(f"API: {args.api_url}")
    print("=" * 64)

    client = ProvenClient(args.api_url, args.api_key)

    # Quick connectivity check
    try:
        r = requests.get(f"{args.api_url}/api/v1/status", timeout=5)
        r.raise_for_status()
        print("  API connectivity: OK\n")
    except Exception as e:
        print(f"\nERROR: Cannot reach API at {args.api_url}: {e}")
        sys.exit(1)

    images = load_images(None, n=args.n)

    robustness = run_robustness_benchmark(client, images)
    correctness = run_false_positive_benchmark(client, n=args.fp_n)
    latency = run_latency_benchmark(client, n=args.latency_n)

    # Build reports
    text_report = build_text_report(robustness, correctness, latency)
    html_report = build_html_report(robustness, correctness, latency, text_report)

    # Save results
    json_path = RESULTS_DIR / f"results_{TIMESTAMP}.json"
    txt_path = RESULTS_DIR / f"report_{TIMESTAMP}.txt"
    html_path = RESULTS_DIR / f"report_{TIMESTAMP}.html"

    json_path.write_text(json.dumps({
        "timestamp": TIMESTAMP,
        "robustness": {k: asdict(v) for k, v in robustness.items()},
        "correctness": asdict(correctness),
        "latency": asdict(latency),
    }, indent=2), encoding="utf-8")
    txt_path.write_text(text_report, encoding="utf-8")
    html_path.write_text(html_report, encoding="utf-8")

    print("\n" + text_report)
    print(f"\nResults saved to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
