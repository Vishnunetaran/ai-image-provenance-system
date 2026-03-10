#!/usr/bin/env python3
"""
scripts/run_benchmarks.py
==========================
Runs all PROVENA Trinity benchmarks and generates the consolidated report.

Usage:
    python scripts/run_benchmarks.py
    python scripts/run_benchmarks.py --n-images 50 --report-pdf
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run(cmd: list, label: str) -> bool:
    print(f"\n▶ {label}")
    print(f"  {' '.join(str(c) for c in cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT))
    elapsed = time.time() - t0
    if result.returncode == 0:
        print(f"  ✅ Done in {elapsed:.0f}s")
        return True
    else:
        print(f"  ❌ Failed (exit {result.returncode})")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-images",   type=int, default=20, help="Images per benchmark")
    parser.add_argument("--report-pdf", action="store_true",  help="Generate PDF report")
    parser.add_argument("--skip-latent",action="store_true",  help="Skip latent benchmark")
    parser.add_argument("--skip-ensemble",action="store_true",help="Skip ensemble benchmark")
    args = parser.parse_args()

    py = sys.executable
    results_dir = ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    print("PROVENA Trinity — Benchmark Suite")
    print("=" * 60)

    if not args.skip_latent:
        run(
            [py, "benchmarks/latent_benchmark.py",
             "--n-images", str(args.n_images),
             "--output", "results/latent_benchmark.json"],
            "Latent Watermark Benchmark"
        )

    if not args.skip_ensemble:
        run(
            [py, "benchmarks/ensemble_benchmark.py",
             "--n-images", str(args.n_images),
             "--output", "results/ensemble_benchmark.json"],
            "Ensemble Detection Rate Benchmark"
        )

    pdf_flag = ["--pdf"] if args.report_pdf else []
    run(
        [py, "benchmarks/generate_report.py"] + pdf_flag,
        "Generating HTML/Markdown Report"
    )

    print("\n✅ All benchmarks complete. Reports in reports/")
