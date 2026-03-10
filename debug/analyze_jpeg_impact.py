"""
Debug Utility: JPEG Impact Analysis.

Analyses which frequency coefficients are destroyed by JPEG compression
at various quality levels.  Helps understand why watermarks degrade and
which frequency bands to target for robust embedding.

Usage:
    python debug/analyze_jpeg_impact.py --image test.png
    python debug/analyze_jpeg_impact.py --image test.png --qualities 10,30,50,75,90
"""

import argparse
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import cv2
except ImportError:
    print("OpenCV required: pip install opencv-python")
    sys.exit(1)


def analyze_jpeg_impact(
    image: np.ndarray,
    qualities: list = None,
) -> dict:
    """Analyse JPEG compression impact on frequency coefficients.

    Args:
        image: Input image (BGR).
        qualities: JPEG quality levels to test.

    Returns:
        Dictionary with per-quality analysis.
    """
    if qualities is None:
        qualities = [10, 30, 50, 75, 90]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray_f = gray.astype(np.float64)

    # Reference FFT
    ref_fft = np.fft.fftshift(np.fft.fft2(gray_f))
    ref_mag = np.abs(ref_fft)

    results = {}

    for q in qualities:
        # Apply JPEG
        tmp = tempfile.mktemp(suffix=".jpg")
        try:
            cv2.imwrite(tmp, image, [cv2.IMWRITE_JPEG_QUALITY, q])
            compressed = cv2.imread(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

        gray_c = cv2.cvtColor(compressed, cv2.COLOR_BGR2GRAY) if compressed.ndim == 3 else compressed
        comp_fft = np.fft.fftshift(np.fft.fft2(gray_c.astype(np.float64)))
        comp_mag = np.abs(comp_fft)

        # Coefficient retention per frequency band
        H, W = ref_mag.shape
        cy, cx = H // 2, W // 2

        bands = {
            "DC (0-2)": (0, 2),
            "very_low (2-5)": (2, 5),
            "low (5-10)": (5, 10),
            "mid_low (10-15)": (10, 15),
            "mid (15-25)": (15, 25),
            "mid_high (25-40)": (25, 40),
            "high (40-60)": (40, 60),
            "very_high (60+)": (60, max(H, W) // 2),
        }

        band_retention = {}
        for name, (r_min, r_max) in bands.items():
            y = np.arange(H) - cy
            x = np.arange(W) - cx
            yy, xx = np.meshgrid(y, x, indexing="ij")
            radius = np.sqrt(xx**2 + yy**2)
            mask = (radius >= r_min) & (radius < r_max)

            if mask.sum() == 0:
                continue

            ref_band = ref_mag[mask]
            comp_band = comp_mag[mask]

            ref_energy = np.sum(ref_band**2)
            if ref_energy < 1e-10:
                retention = 1.0
            else:
                retention = np.sum(comp_band**2) / ref_energy

            band_retention[name] = round(float(retention), 4)

        # Overall PSNR
        mse = np.mean((gray_f - gray_c.astype(np.float64)) ** 2)
        psnr = 10 * np.log10(255 ** 2 / (mse + 1e-10))

        results[f"Q={q}"] = {
            "psnr_db": round(float(psnr), 2),
            "band_retention": band_retention,
            "total_energy_retention": round(
                float(np.sum(comp_mag**2) / (np.sum(ref_mag**2) + 1e-10)), 4
            ),
        }

    return results


def print_report(results: dict):
    """Print formatted JPEG impact report."""
    print("\n" + "=" * 70)
    print("  JPEG Compression Impact Analysis")
    print("=" * 70)

    for quality, data in results.items():
        print(f"\n── {quality} (PSNR: {data['psnr_db']}dB) ─────────────────────")
        print(f"  Total energy retention: {data['total_energy_retention']:.2%}")
        print()

        for band, retention in data["band_retention"].items():
            bar_len = int(retention * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            status = "✅" if retention > 0.8 else ("⚠️" if retention > 0.5 else "❌")
            print(f"  {band:25s} [{bar}] {retention:.1%} {status}")

    print("\n" + "=" * 70)
    print("  Legend: ✅ >80% retained  ⚠️ 50-80% retained  ❌ <50% retained")
    print("  Recommendation: Target mid (15-25) and mid_low (10-15) bands")
    print("=" * 70)


def create_impact_visualization(
    image: np.ndarray,
    qualities: list = None,
) -> np.ndarray:
    """Create visual comparison across quality levels.

    Args:
        image: Input image.
        qualities: Quality levels.

    Returns:
        Combined visualisation image.
    """
    if qualities is None:
        qualities = [10, 30, 50, 75, 90]

    size = 200
    panels = []

    for q in qualities:
        tmp = tempfile.mktemp(suffix=".jpg")
        try:
            cv2.imwrite(tmp, image, [cv2.IMWRITE_JPEG_QUALITY, q])
            compressed = cv2.imread(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

        small = cv2.resize(compressed, (size, size))

        # Add quality label
        cv2.putText(
            small, f"Q={q}", (5, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )

        # Compute PSNR
        orig_small = cv2.resize(image, (size, size))
        mse = np.mean((orig_small.astype(float) - small.astype(float)) ** 2)
        psnr = 10 * np.log10(255**2 / (mse + 1e-10))
        cv2.putText(
            small, f"PSNR:{psnr:.1f}", (5, size - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1,
        )

        panels.append(small)

    return np.hstack(panels)


def main():
    parser = argparse.ArgumentParser(description="Analyze JPEG impact on frequency coefficients")
    parser.add_argument("--image", type=str, help="Input image path")
    parser.add_argument("--qualities", type=str, default="10,30,50,75,90")
    parser.add_argument("--output", type=str, default="debug/jpeg_impact.png")
    args = parser.parse_args()

    qualities = [int(q) for q in args.qualities.split(",")]

    if args.image and os.path.exists(args.image):
        image = cv2.imread(args.image)
    else:
        # Generate a test image
        print("No image specified; using synthetic test image.")
        image = np.zeros((256, 256, 3), dtype=np.uint8)
        for i in range(256):
            for j in range(256):
                image[i, j, 0] = int(128 + 80 * np.sin(i / 10.0))
                image[i, j, 1] = int(128 + 80 * np.cos(j / 10.0))
                image[i, j, 2] = int(128 + 60 * np.sin((i + j) / 15.0))

    # Analysis
    results = analyze_jpeg_impact(image, qualities)
    print_report(results)

    # Visualisation
    vis = create_impact_visualization(image, qualities)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    cv2.imwrite(args.output, vis)
    print(f"\nVisualisation saved to {args.output}")


if __name__ == "__main__":
    main()
