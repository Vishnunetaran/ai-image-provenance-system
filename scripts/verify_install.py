#!/usr/bin/env python3
"""
scripts/verify_install.py
===========================
Checks that all required dependencies are importable and
reports which Trinity layers are available.

Usage:
    python scripts/verify_install.py
    python scripts/verify_install.py --models-dir models/
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def _check(label: str, fn) -> bool:
    try:
        fn()
        print(f"  {GREEN}✅{RESET} {label}")
        return True
    except Exception as exc:
        print(f"  {RED}❌{RESET} {label}: {exc}")
        return False


def check_core_deps() -> int:
    """Check core Python dependencies."""
    print(f"\n{BOLD}── Core Dependencies ──────────────────────────────────────{RESET}")
    deps = [
        ("flask",       lambda: importlib.import_module("flask")),
        ("cryptography",lambda: importlib.import_module("cryptography")),
        ("PIL (Pillow)",lambda: importlib.import_module("PIL")),
        ("numpy",       lambda: importlib.import_module("numpy")),
        ("cv2 (OpenCV)",lambda: importlib.import_module("cv2")),
        ("imagehash",   lambda: importlib.import_module("imagehash")),
        ("requests",    lambda: importlib.import_module("requests")),
    ]
    failures = sum(0 if _check(lbl, fn) else 1 for lbl, fn in deps)
    return failures


def check_ml_deps() -> int:
    """Check ML dependencies (optional)."""
    print(f"\n{BOLD}── ML Dependencies ────────────────────────────────────────{RESET}")
    deps = [
        ("torch",            lambda: importlib.import_module("torch")),
        ("torchvision",      lambda: importlib.import_module("torchvision")),
        ("timm",             lambda: importlib.import_module("timm")),
        ("einops",           lambda: importlib.import_module("einops")),
        ("transformers",     lambda: importlib.import_module("transformers")),
        ("diffusers",        lambda: importlib.import_module("diffusers")),
        ("lpips",            lambda: importlib.import_module("lpips")),
    ]
    failures = 0
    for lbl, fn in deps:
        try:
            fn()
            print(f"  {GREEN}✅{RESET} {lbl}")
        except Exception as exc:
            print(f"  {YELLOW}⚠️ {RESET} {lbl}: Not installed (optional)")
            failures += 1
    return failures


def check_model_files(models_dir: str) -> None:
    """Check which model weight files are present."""
    print(f"\n{BOLD}── Model Weight Files ─────────────────────────────────────{RESET}")
    expected = [
        ("pixel_encoder.pth",  "Pixel WM encoder (required for full mode)"),
        ("pixel_decoder.pth",  "Pixel WM decoder (required for full mode)"),
        ("cnn_classifier.pth", "CNN classifier   (required for standard/deep)"),
        ("vit_detector.pth",   "ViT detector     (required for deep mode)"),
    ]
    models_path = Path(models_dir)
    all_found = True
    for fname, desc in expected:
        fpath = models_path / fname
        if fpath.exists():
            size_mb = fpath.stat().st_size / 1_048_576
            print(f"  {GREEN}✅{RESET} {fname:<28} {desc} ({size_mb:.0f} MB)")
        else:
            print(f"  {YELLOW}⚠️ {RESET} {fname:<28} {desc} — NOT FOUND")
            all_found = False
    if not all_found:
        print(f"\n  {YELLOW}ℹ{RESET}  Missing models? Run: python scripts/download_models.py")
        print(f"  {YELLOW}ℹ{RESET}  Or train: see docs/TRAINING_GUIDE.md")


def check_env_vars() -> None:
    """Check critical environment variables."""
    print(f"\n{BOLD}── Environment Variables ──────────────────────────────────{RESET}")
    variables = [
        ("SECRET_KEY",        "Flask session security", True),
        ("TRINITY_SECRET_KEY","Latent watermark CSPRNG key", True),
        ("DATABASE_PATH",     "SQLite provenance DB path", False),
        ("DEVICE",            "Compute device (cuda/cpu)", False),
    ]
    for name, desc, required in variables:
        val = os.environ.get(name)
        if val:
            masked = val[:4] + "****" if len(val) > 4 else "****"
            print(f"  {GREEN}✅{RESET} {name:<22} {desc} = {masked}")
        elif required:
            print(f"  {YELLOW}⚠️ {RESET} {name:<22} {desc} — NOT SET (using default)")
        else:
            print(f"  {YELLOW}·· {RESET} {name:<22} {desc} — optional")


def check_flask_import() -> bool:
    """Try to import the Flask app."""
    print(f"\n{BOLD}── Flask App Import ───────────────────────────────────────{RESET}")
    try:
        os.environ.setdefault("TRINITY_SECRET_KEY",
                              "4a6f736570687175657a6f6e6e6561626c656b657968657831323334" + "0000")
        from provena_flask import create_app
        app = create_app("development")
        mode = getattr(app, "trinity", None)
        trinity_mode = mode.mode if mode else "unknown"
        print(f"  {GREEN}✅{RESET} Flask app created successfully")
        print(f"  {GREEN}✅{RESET} Trinity mode: {BOLD}{trinity_mode}{RESET}")

        if mode:
            layers = mode.available_layers
            for layer, available in layers.items():
                sym = f"{GREEN}✅{RESET}" if available else f"{YELLOW}⚠️ {RESET}"
                print(f"     {sym}  {layer}")
        return True
    except Exception as exc:
        print(f"  {RED}❌{RESET} Flask import failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def check_gpu() -> None:
    """Report GPU availability."""
    print(f"\n{BOLD}── GPU / CUDA ─────────────────────────────────────────────{RESET}")
    try:
        import torch
        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            names = [torch.cuda.get_device_name(i) for i in range(n)]
            print(f"  {GREEN}✅{RESET} CUDA available: {n} device(s): {', '.join(names)}")
        else:
            print(f"  {YELLOW}·· {RESET} CUDA not available — running on CPU")
    except ImportError:
        print(f"  {YELLOW}⚠️ {RESET} torch not installed — cannot check CUDA")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", default="models/", help="Path to models directory")
    parser.add_argument("--skip-app",   action="store_true", help="Skip Flask app import check")
    args = parser.parse_args()

    print(f"{BOLD}PROVENA Trinity — Installation Verification{RESET}")
    print("=" * 60)

    core_fails = check_core_deps()
    ml_fails   = check_ml_deps()
    check_model_files(args.models_dir)
    check_env_vars()
    check_gpu()

    if not args.skip_app:
        check_flask_import()

    print(f"\n{'=' * 60}")
    if core_fails == 0:
        print(f"{GREEN}✅ Core installation OK{RESET}")
    else:
        print(f"{RED}❌ {core_fails} core dependency/ies missing — run: pip install -r requirements.txt{RESET}")

    if ml_fails > 0:
        print(f"{YELLOW}⚠️  {ml_fails} optional ML libraries missing.{RESET}")
        print(f"   System will run in {BOLD}latent_only{RESET} mode.")
        print(f"   For full functionality: pip install torch torchvision timm einops")
    else:
        print(f"{GREEN}✅ All ML libraries installed{RESET}")
