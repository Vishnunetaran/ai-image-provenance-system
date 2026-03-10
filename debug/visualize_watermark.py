"""
Debug Utility: Watermark Visualization.

Visualizes watermark patterns in multiple domains:
    - Spatial domain (pixel-space residual)
    - DWT decomposition (subband view)
    - FFT magnitude spectrum (ring detection)
    - DCT coefficient heatmap

Usage:
    python debug/visualize_watermark.py --image watermarked.png --key-hex 0000...
"""

import argparse
import sys
import os

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import cv2
except ImportError:
    print("OpenCV required: pip install opencv-python")
    sys.exit(1)


def visualize_fft_spectrum(image: np.ndarray, title: str = "FFT Magnitude") -> np.ndarray:
    """Create FFT magnitude spectrum visualisation.

    Args:
        image: Grayscale uint8 image.
        title: Title for the plot.

    Returns:
        Visualisation image (H, W) uint8.
    """
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # Compute FFT
    f = np.fft.fft2(gray.astype(np.float64))
    fshift = np.fft.fftshift(f)
    magnitude = np.log1p(np.abs(fshift))

    # Normalise to 0-255
    magnitude = (magnitude / magnitude.max() * 255).astype(np.uint8)

    # Apply colourmap
    coloured = cv2.applyColorMap(magnitude, cv2.COLORMAP_JET)

    # Draw rings for watermark region
    h, w = magnitude.shape
    center = (w // 2, h // 2)
    cv2.circle(coloured, center, 5, (0, 255, 0), 1)   # Inner ring
    cv2.circle(coloured, center, 20, (0, 255, 0), 1)  # Outer ring

    return coloured


def visualize_ring_difference(
    original: np.ndarray, watermarked: np.ndarray
) -> np.ndarray:
    """Visualize the difference in the FFT ring region between two images.

    Args:
        original: Original image.
        watermarked: Watermarked image.

    Returns:
        Difference heatmap.
    """
    g1 = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY) if original.ndim == 3 else original
    g2 = cv2.cvtColor(watermarked, cv2.COLOR_BGR2GRAY) if watermarked.ndim == 3 else watermarked

    f1 = np.fft.fftshift(np.fft.fft2(g1.astype(np.float64)))
    f2 = np.fft.fftshift(np.fft.fft2(g2.astype(np.float64)))

    diff = np.log1p(np.abs(f2) - np.abs(f1) + 1e-10)
    diff = diff - diff.min()
    if diff.max() > 0:
        diff = diff / diff.max()
    diff = (diff * 255).astype(np.uint8)

    return cv2.applyColorMap(diff, cv2.COLORMAP_HOT)


def visualize_pixel_residual(
    original: np.ndarray, watermarked: np.ndarray, amplify: float = 20.0
) -> np.ndarray:
    """Visualize the pixel-space difference between original and watermarked.

    Args:
        original: Original image.
        watermarked: Watermarked image.
        amplify: Amplification factor for visibility.

    Returns:
        Amplified residual image.
    """
    diff = watermarked.astype(np.float64) - original.astype(np.float64)
    diff = np.abs(diff) * amplify
    diff = np.clip(diff, 0, 255).astype(np.uint8)
    return diff


def visualize_dwt_subbands(image: np.ndarray) -> np.ndarray:
    """Visualize DWT decomposition subbands.

    Args:
        image: Input image.

    Returns:
        Combined subband visualisation.
    """
    try:
        import pywt
    except ImportError:
        print("PyWavelets required: pip install PyWavelets")
        return image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    coeffs = pywt.dwt2(gray.astype(np.float64), "haar")
    cA, (cH, cV, cD) = coeffs

    def norm(arr):
        arr = np.abs(arr)
        if arr.max() > 0:
            arr = arr / arr.max() * 255
        return arr.astype(np.uint8)

    h, w = cA.shape

    # Compose: [cA, cH; cV, cD]
    result = np.zeros((h * 2, w * 2), dtype=np.uint8)
    result[:h, :w] = norm(cA)
    result[:h, w:] = norm(cH)
    result[h:, :w] = norm(cV)
    result[h:, w:] = norm(cD)

    # Add labels
    result_color = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(result_color, "cA", (5, 20), font, 0.5, (0, 255, 0), 1)
    cv2.putText(result_color, "cH", (w + 5, 20), font, 0.5, (0, 255, 0), 1)
    cv2.putText(result_color, "cV", (5, h + 20), font, 0.5, (0, 255, 0), 1)
    cv2.putText(result_color, "cD", (w + 5, h + 20), font, 0.5, (0, 255, 0), 1)

    return result_color


def create_debug_panel(
    original: np.ndarray,
    watermarked: np.ndarray,
) -> np.ndarray:
    """Create a full debug panel with all visualizations.

    Args:
        original: Original image.
        watermarked: Watermarked image.

    Returns:
        Combined debug panel image.
    """
    size = 256

    # Resize to consistent size
    orig_small = cv2.resize(original, (size, size))
    wm_small = cv2.resize(watermarked, (size, size))

    # Generate visualisations
    fft_orig = cv2.resize(visualize_fft_spectrum(orig_small), (size, size))
    fft_wm = cv2.resize(visualize_fft_spectrum(wm_small), (size, size))
    residual = cv2.resize(visualize_pixel_residual(orig_small, wm_small), (size, size))
    ring_diff = cv2.resize(visualize_ring_difference(orig_small, wm_small), (size, size))

    # Compose 2x3 grid
    row1 = np.hstack([orig_small, wm_small, residual])
    row2 = np.hstack([fft_orig, fft_wm, ring_diff])
    panel = np.vstack([row1, row2])

    return panel


def main():
    parser = argparse.ArgumentParser(description="Visualize watermark embedding")
    parser.add_argument("--original", type=str, help="Original image path")
    parser.add_argument("--watermarked", type=str, help="Watermarked image path")
    parser.add_argument("--output", type=str, default="debug/debug_panel.png")
    args = parser.parse_args()

    if args.original and args.watermarked:
        original = cv2.imread(args.original)
        watermarked = cv2.imread(args.watermarked)

        if original is None or watermarked is None:
            print("Error: Could not load images")
            sys.exit(1)

        panel = create_debug_panel(original, watermarked)
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        cv2.imwrite(args.output, panel)
        print(f"Debug panel saved to {args.output}")
    else:
        # Demo mode: generate synthetic images
        print("Demo mode: generating synthetic watermarked image...")
        from src.watermarking.latent.pattern_generator import generate_pattern

        key = b"\x42" * 32
        H, W = 256, 256

        original = np.random.randint(50, 200, (H, W, 3), dtype=np.uint8)
        pattern = generate_pattern("demo-001", key, (H, W), apply_freq_shaping=False).numpy()
        watermarked = original.copy().astype(np.float64)
        for c in range(3):
            watermarked[:, :, c] += pattern * 30
        watermarked = np.clip(watermarked, 0, 255).astype(np.uint8)

        panel = create_debug_panel(original, watermarked)
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        cv2.imwrite(args.output, panel)
        print(f"Debug panel saved to {args.output}")


if __name__ == "__main__":
    main()
