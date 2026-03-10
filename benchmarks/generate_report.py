"""
benchmarks/generate_report.py
================================
Aggregates JSON results from all benchmarks and generates a
Markdown + optional PDF report.

Usage:
    python benchmarks/generate_report.py
    python benchmarks/generate_report.py --pdf --output reports/Trinity_Benchmark_Report.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"


# ─────────────────────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────────────────────

def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if path.exists():
        return json.loads(path.read_text())
    return None


def generate_markdown_report(
    latent: Optional[Dict],
    ensemble: Optional[Dict],
) -> str:
    lines = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines.append("# PROVENA Trinity — Benchmark Report\n")
    lines.append(f"**Generated:** {now}\n")
    lines.append(
        "| Layer | Available | Status |\n"
        "|-------|-----------|--------|\n"
        "| Latent Watermark  | Always | ✅ |\n"
        "| Perceptual Hash   | Always | ✅ |\n"
        "| CNN Classifier    | Requires .pth | 🔄 |\n"
        "| ViT Detector      | Requires .pth | 🔄 |\n"
    )

    # ── Latent benchmark ──────────────────────────────────────────────────
    if latent:
        lines.append("\n---\n\n## 1. Latent Watermark Robustness\n")
        lines.append(f"**Images tested:** {latent.get('n_images', 'N/A')}  "
                     f"**Transforms:** {latent.get('n_transforms', 'N/A')}\n")
        lines.append(
            "| Transform | Detection Rate | Avg Latency (ms) |\n"
            "|-----------|:--------------:|:----------------:|\n"
        )
        for name, stats in latent.get("results", {}).items():
            rate_pct = stats["detection_rate"] * 100
            emoji = "✅" if rate_pct >= 90 else ("⚠️" if rate_pct >= 70 else "❌")
            lines.append(
                f"| {name:<25} | {emoji} {rate_pct:5.1f}% | "
                f"{stats['avg_latency_ms']:6.1f} |\n"
            )
    else:
        lines.append("\n## 1. Latent Watermark Robustness\n")
        lines.append("> ⚠️ No results found. Run: `python benchmarks/latent_benchmark.py`\n")

    # ── Ensemble benchmark ────────────────────────────────────────────────
    if ensemble:
        lines.append("\n---\n\n## 2. Ensemble Detection Rate\n")
        n = ensemble.get("n_images", "N/A")
        lines.append(f"**Registered images:** {n}  **Unregistered:** {n}\n")
        lines.append(
            "| Mode | TPR | FPR | Avg Latency (ms) |\n"
            "|------|:---:|:---:|:----------------:|\n"
        )
        for mode, m in ensemble.get("modes", {}).items():
            fpr_emoji = "✅" if m["fpr"] < 0.001 else ("⚠️" if m["fpr"] < 0.01 else "❌")
            lines.append(
                f"| {mode:<8} | {m['tpr']*100:5.1f}% | "
                f"{fpr_emoji} {m['fpr']*100:.3f}% | {m['avg_detect_ms']:5.0f} |\n"
            )
    else:
        lines.append("\n## 2. Ensemble Detection Rate\n")
        lines.append("> ⚠️ No results found. Run: `python benchmarks/ensemble_benchmark.py`\n")

    # ── Performance targets ────────────────────────────────────────────────
    lines.append("\n---\n\n## 3. Performance Targets\n")
    lines.append(
        "| Metric | Target | Status |\n"
        "|--------|--------|--------|\n"
        "| Latent detection (clean)         | ≥ 99%  | 🔄 See above |\n"
        "| Latent detection (JPEG Q=50)     | ≥ 95%  | 🔄 See above |\n"
        "| Pixel detection (clean)          | ≥ 99%  | ⏳ Needs training |\n"
        "| False positive rate              | < 0.1% | 🔄 See above |\n"
        "| Fast mode latency (CPU)          | < 50ms | 🔄 See above |\n"
        "| Standard mode latency (CPU)      | < 200ms | 🔄 See above |\n"
        "| Deep mode latency (GPU)          | < 500ms | ⏳ Needs GPU |\n"
    )

    lines.append("\n---\n\n## 4. Next Steps\n")
    lines.append(
        "1. Train models in Colab (see `docs/TRAINING_GUIDE.md`)\n"
        "2. Download weights to `models/*.pth`\n"
        "3. Re-run benchmarks: `bash scripts/run_benchmarks.sh`\n"
        "4. Target: TPR ≥ 99%, FPR < 0.1%, standard-mode < 200ms\n"
    )

    return "".join(lines)


def save_pdf(md_text: str, output: Path) -> bool:
    """Attempt to save PDF via reportlab. Returns True on success."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm

        output.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(str(output), pagesize=A4,
                                leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        story = []
        for line in md_text.split("\n"):
            if line.startswith("# "):
                story.append(Paragraph(line[2:], styles["Title"]))
            elif line.startswith("## "):
                story.append(Spacer(1, 4*mm))
                story.append(Paragraph(line[3:], styles["Heading2"]))
            elif line.strip():
                safe_line = line.replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(safe_line, styles["Normal"]))
            else:
                story.append(Spacer(1, 2*mm))
        doc.build(story)
        return True
    except ImportError:
        print("⚠️  reportlab not installed. Install with: pip install reportlab")
        return False
    except Exception as exc:
        print(f"⚠️  PDF generation failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Trinity benchmark report")
    parser.add_argument("--pdf",    action="store_true", help="Also generate PDF")
    parser.add_argument("--output", type=str, default="reports/Trinity_Benchmark_Report.pdf")
    args = parser.parse_args()

    latent   = load_json(RESULTS_DIR / "latent_benchmark.json")
    ensemble = load_json(RESULTS_DIR / "ensemble_benchmark.json")

    md = generate_markdown_report(latent, ensemble)

    # Save Markdown
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORTS_DIR / "Trinity_Benchmark_Report.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"✅ Markdown report saved to {md_path}")
    print(md)

    if args.pdf:
        pdf_path = Path(args.output)
        if save_pdf(md, pdf_path):
            print(f"✅ PDF report saved to {pdf_path}")
