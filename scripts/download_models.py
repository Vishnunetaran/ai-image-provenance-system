#!/usr/bin/env python3
"""
scripts/download_models.py
===========================
Downloads trained PROVENA Trinity model weights from a given source.

Usage:
    python scripts/download_models.py                     # all models
    python scripts/download_models.py --model encoder     # single model
    python scripts/download_models.py --source gdrive     # Google Drive
    python scripts/download_models.py --list              # list available

NOTE: Requires actual model hosting. Until public models are released,
this script will either use the MODELS_BASE_URL env var or skip gracefully.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Model registry ─────────────────────────────────────────────────────────
# Update these with your actual download links after training

MODELS: Dict[str, Dict] = {
    "encoder": {
        "file":        "pixel_encoder.pth",
        "description": "Pixel watermark encoder (CNN+ViT hybrid)",
        "size_mb":     200,
        "sha256":      None,   # fill after training
        "url":         os.environ.get("ENCODER_URL", ""),
        "gdrive_id":   os.environ.get("ENCODER_GDRIVE_ID", ""),
    },
    "decoder": {
        "file":        "pixel_decoder.pth",
        "description": "Pixel watermark decoder",
        "size_mb":     80,
        "sha256":      None,
        "url":         os.environ.get("DECODER_URL", ""),
        "gdrive_id":   os.environ.get("DECODER_GDRIVE_ID", ""),
    },
    "cnn": {
        "file":        "cnn_classifier.pth",
        "description": "CNN-based AI classifier (EfficientNet-B4)",
        "size_mb":     80,
        "sha256":      None,
        "url":         os.environ.get("CNN_URL", ""),
        "gdrive_id":   os.environ.get("CNN_GDRIVE_ID", ""),
    },
    "vit": {
        "file":        "vit_detector.pth",
        "description": "ViT global detector (ViT-Small)",
        "size_mb":     85,
        "sha256":      None,
        "url":         os.environ.get("VIT_URL", ""),
        "gdrive_id":   os.environ.get("VIT_GDRIVE_ID", ""),
    },
}


def _verify_sha256(path: Path, expected: Optional[str]) -> bool:
    """Verify file integrity using SHA-256."""
    if expected is None:
        return True   # no checksum available; skip
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != expected:
        print(f"  ❌ SHA-256 mismatch: expected {expected[:12]}… got {actual[:12]}…")
        return False
    return True


def _download_http(url: str, dest: Path) -> bool:
    """Download via HTTP with progress."""
    try:
        import requests
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        t0 = time.time()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    speed = downloaded / (time.time() - t0 + 1e-9) / 1_048_576
                    print(f"  ↓ {pct:5.1f}%  {speed:.1f} MB/s", end="\r", flush=True)
        print()
        return True
    except Exception as exc:
        print(f"  ❌ HTTP download failed: {exc}")
        return False


def _download_gdrive(file_id: str, dest: Path) -> bool:
    """Download from Google Drive using gdown."""
    try:
        import gdown
        url = f"https://drive.google.com/uc?id={file_id}"
        gdown.download(url, str(dest), quiet=False)
        return dest.exists()
    except ImportError:
        print("  ❌ gdown not installed. Run: pip install gdown")
        return False
    except Exception as exc:
        print(f"  ❌ Google Drive download failed: {exc}")
        return False


def download_model(name: str, info: Dict, models_dir: Path, source: str = "url") -> bool:
    """Download a single model."""
    dest = models_dir / info["file"]

    if dest.exists():
        print(f"  ✅ {info['file']} already exists ({dest.stat().st_size // 1048576} MB)")
        return True

    print(f"\n  📥 Downloading {name}: {info['description']}")
    print(f"     → {dest}")

    success = False
    if source == "gdrive" and info.get("gdrive_id"):
        success = _download_gdrive(info["gdrive_id"], dest)
    elif info.get("url"):
        success = _download_http(info["url"], dest)
    else:
        print(f"  ⚠️  No download URL configured for '{name}'")
        print(f"     Set env var {name.upper()}_URL or {name.upper()}_GDRIVE_ID")
        return False

    if success and info.get("sha256"):
        print(f"  🔍 Verifying checksum…")
        if not _verify_sha256(dest, info["sha256"]):
            dest.unlink(missing_ok=True)
            return False
        print(f"  ✅ Checksum OK")

    return success


def list_models() -> None:
    print("\nAvailable PROVENA Trinity model weights:\n")
    print(f"  {'Name':<10} {'File':<28} {'Size':<10} {'Description'}")
    print(f"  {'-'*10} {'-'*28} {'-'*10} {'-'*30}")
    for name, info in MODELS.items():
        print(f"  {name:<10} {info['file']:<28} ~{info['size_mb']} MB  {info['description']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download PROVENA Trinity model weights")
    parser.add_argument("--model",      choices=list(MODELS.keys()) + ["all"], default="all")
    parser.add_argument("--source",     choices=["url", "gdrive"], default="url")
    parser.add_argument("--models-dir", default="models/")
    parser.add_argument("--list",       action="store_true")
    args = parser.parse_args()

    if args.list:
        list_models()
        sys.exit(0)

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    targets = {args.model: MODELS[args.model]} if args.model != "all" else MODELS

    print(f"PROVENA Trinity — Model Downloader")
    print(f"Models directory: {models_dir.resolve()}")

    results = {}
    for name, info in targets.items():
        ok = download_model(name, info, models_dir, args.source)
        results[name] = ok

    print("\n── Summary ─────────────────────────────────────────────────")
    for name, ok in results.items():
        status = "✅ OK" if ok else "❌ FAILED"
        print(f"  {name:<10} {status}")

    if all(results.values()):
        print("\n✅ All models ready. Run: python scripts/verify_install.py")
    else:
        failed = [n for n, ok in results.items() if not ok]
        print(f"\n⚠️  Failed: {', '.join(failed)}")
        print("   Train models locally: see docs/TRAINING_GUIDE.md")
