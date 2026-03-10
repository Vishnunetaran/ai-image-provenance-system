"""
benchmarks/ensemble_benchmark.py
==================================
End-to-end detection rate benchmark for the Trinity ensemble API.

Measures:
  - TPR (registered images correctly detected)
  - FPR (unregistered images falsely detected — target < 0.1%)
  - Latency per mode

Usage:
    python benchmarks/ensemble_benchmark.py
    python benchmarks/ensemble_benchmark.py --n-images 50 --output results/ensemble.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
from PIL import Image as PILImage

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_b64_image(seed: int = 0, size: int = 64) -> str:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, "PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _register_image(client, b64: str, model_id: str = "bench-model") -> Dict[str, Any]:
    resp = client.post(
        "/api/v2/images/register",
        json={"image": b64, "model_id": model_id},
    )
    return json.loads(resp.data) if resp.status_code == 201 else {}


def _detect_image(client, b64: str, mode: str = "standard") -> Dict[str, Any]:
    resp = client.post(
        "/api/v2/images/detect",
        json={"image": b64, "mode": mode},
    )
    return json.loads(resp.data) if resp.status_code == 200 else {"status": "ERROR"}


def run_benchmark(n_images: int = 20) -> Dict[str, Any]:
    os.environ.setdefault(
        "TRINITY_SECRET_KEY",
        "4a6f736570687175657a6f6e6e6561626c656b657968657831323334" + "0000",
    )
    from provena_flask import create_app
    app = create_app("testing")
    client = app.test_client()
    client.__enter__()

    results: Dict[str, Any] = {
        "benchmark": "ensemble_detection_rate",
        "n_images":  n_images,
        "modes":     {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    for mode in ["fast", "standard", "deep"]:
        print(f"\n▶ Mode: {mode} | {n_images} registered + {n_images} unregistered images")

        tp_count = 0
        fp_count = 0
        fn_count = 0
        tn_count = 0
        reg_latencies: List[float] = []
        det_latencies: List[float] = []

        # ── Registered images → should detect ────────────────────────────
        for i in range(n_images):
            b64 = _make_b64_image(seed=i)
            reg_resp = _register_image(client, b64)
            if not reg_resp.get("image_id"):
                continue

            # Detect the same watermarked image
            wm_b64 = reg_resp.get("watermarked_image", b64)
            t0 = time.perf_counter()
            det = _detect_image(client, wm_b64, mode=mode)
            det_latencies.append((time.perf_counter() - t0) * 1000)

            status = det.get("status", "NOT_DETECTED")
            if status in ("VERIFIED", "LIKELY_VERIFIED"):
                tp_count += 1
            else:
                fn_count += 1

        # ── Unregistered images → should NOT detect ───────────────────────
        for i in range(n_images, n_images * 2):
            b64 = _make_b64_image(seed=i + 10000)   # different seeds
            t0 = time.perf_counter()
            det = _detect_image(client, b64, mode=mode)
            det_latencies.append((time.perf_counter() - t0) * 1000)

            status = det.get("status", "NOT_DETECTED")
            if status in ("VERIFIED", "LIKELY_VERIFIED"):
                fp_count += 1
            else:
                tn_count += 1

        total_pos = tp_count + fn_count
        total_neg = fp_count + tn_count

        tpr = tp_count / total_pos if total_pos else 0.0
        fpr = fp_count / total_neg if total_neg else 0.0

        results["modes"][mode] = {
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
            "tp":  tp_count,
            "fn":  fn_count,
            "fp":  fp_count,
            "tn":  tn_count,
            "avg_detect_ms": round(float(np.mean(det_latencies)), 1) if det_latencies else 0.0,
            "p95_detect_ms": round(float(np.percentile(det_latencies, 95)), 1) if det_latencies else 0.0,
        }

        print(f"  TPR: {tpr*100:.1f}%  FPR: {fpr*100:.3f}%  "
              f"avg latency: {results['modes'][mode]['avg_detect_ms']:.0f} ms")

    client.__exit__(None, None, None)
    return results


def print_markdown_report(data: Dict[str, Any]) -> None:
    print(f"\n## Trinity Ensemble Detection Rate Report")
    print(f"**Images:** {data['n_images']} registered + {data['n_images']} unregistered\n")
    print(f"| Mode | TPR | FPR | Avg Latency |")
    print(f"|------|:---:|:---:|:-----------:|")
    for mode, m in data["modes"].items():
        fpr_ok = "✅" if m["fpr"] < 0.001 else "⚠️"
        print(f"| {mode:<8} | {m['tpr']*100:5.1f}% | {fpr_ok} {m['fpr']*100:.3f}% | {m['avg_detect_ms']:5.0f} ms |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-images", type=int, default=20)
    parser.add_argument("--output",   type=str, default="results/ensemble_benchmark.json")
    args = parser.parse_args()

    data = run_benchmark(args.n_images)
    print_markdown_report(data)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\n✅ Results saved to {out_path}")
