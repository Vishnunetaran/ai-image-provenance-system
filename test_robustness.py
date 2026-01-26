"""
Comprehensive Watermark Robustness Testing Suite.

Tests watermark extraction success rate after various transformations:
- JPEG compression (Q=50, 75, 90)
- Resizing (70%, 80%, 90%, 110%)
- Format conversion (PNG ↔ JPG)
"""

import os
import sys
from pathlib import Path
import numpy as np
import cv2
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.watermark_service import WatermarkService


def create_test_image(size=(512, 512)) -> np.ndarray:
    """Create a complex test image with varied content."""
    image = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    
    # Create gradient + pattern
    for i in range(size[0]):
        for j in range(size[1]):
            image[i, j] = [
                int(255 * i / size[0]),
                int(255 * j / size[1]),
                int(128 + 127 * np.sin(i / 20) * np.cos(j / 20))
            ]
    
    # Add some features
    cv2.circle(image, (256, 256), 100, (255, 255, 255), -1)
    cv2.rectangle(image, (100, 100), (200, 200), (0, 0, 255), 5)
    
    return image


def test_jpeg_compression():
    """Test watermark robustness against JPEG compression."""
    print("\n" + "="*70)
    print("TEST 1: JPEG Compression Robustness")
    print("="*70)
    
    service = WatermarkService(alpha=0.03)
    test_image = create_test_image()
    test_payload = b'img-jpeg-test123'
    
    # Embed watermark
    watermarked = service.embed_watermark(test_image, test_payload)
    
    quality_levels = [50, 75, 90]
    results = {}
    
    temp_dir = tempfile.mkdtemp()
    
    try:
        for quality in quality_levels:
            # Save as JPEG with specific quality
            jpeg_path = os.path.join(temp_dir, f"test_q{quality}.jpg")
            cv2.imwrite(jpeg_path, watermarked, [cv2.IMWRITE_JPEG_QUALITY, quality])
            
            # Load back
            jpeg_image = cv2.imread(jpeg_path)
            
            # Try to extract watermark
            extracted = service.extract_watermark(jpeg_image, payload_length=16)
            
            if extracted:
                match_ratio = sum(a == b for a, b in zip(test_payload, extracted)) / len(test_payload)
                success = match_ratio >= 0.5  # 50% threshold
                results[quality] = {'success': success, 'match': match_ratio}
            else:
                results[quality] = {'success': False, 'match': 0.0}
            
            status = "✓" if results[quality]['success'] else "✗"
            print(f"  Q={quality}: {status} Match: {results[quality]['match']*100:.1f}%")
    
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    # Calculate success rate
    success_count = sum(1 for r in results.values() if r['success'])
    success_rate = success_count / len(quality_levels)
    
    print(f"\nJPEG Compression Success Rate: {success_rate*100:.1f}%")
    print(f"Target: ≥90% (Q≥75)")
    
    return results, success_rate


def test_resizing():
    """Test watermark robustness against resizing."""
    print("\n" + "="*70)
    print("TEST 2: Resizing Robustness")
    print("="*70)
    
    service = WatermarkService(alpha=0.03)
    test_image = create_test_image()
    test_payload = b'img-resize-test1'
    
    # Embed watermark
    watermarked = service.embed_watermark(test_image, test_payload)
    
    scale_factors = [0.7, 0.8, 0.9, 1.1]
    results = {}
    
    h, w = watermarked.shape[:2]
    
    for scale in scale_factors:
        # Resize
        new_size = (int(w * scale), int(h * scale))
        resized = cv2.resize(watermarked, new_size, interpolation=cv2.INTER_LINEAR)
        
        # Resize back to original
        resized_back = cv2.resize(resized, (w, h), interpolation=cv2.INTER_LINEAR)
        
        # Try to extract watermark
        extracted = service.extract_watermark(resized_back, payload_length=16)
        
        if extracted:
            match_ratio = sum(a == b for a, b in zip(test_payload, extracted)) / len(test_payload)
            success = match_ratio >= 0.5
            results[scale] = {'success': success, 'match': match_ratio}
        else:
            results[scale] = {'success': False, 'match': 0.0}
        
        status = "✓" if results[scale]['success'] else "✗"
        print(f"  Scale={scale*100:.0f}%: {status} Match: {results[scale]['match']*100:.1f}%")
    
    # Calculate success rate
    success_count = sum(1 for r in results.values() if r['success'])
    success_rate = success_count / len(scale_factors)
    
    print(f"\nResizing Success Rate: {success_rate*100:.1f}%")
    print(f"Target: ≥90%")
    
    return results, success_rate


def test_format_conversion():
    """Test watermark robustness against format conversion."""
    print("\n" + "="*70)
    print("TEST 3: Format Conversion Robustness")
    print("="*70)
    
    service = WatermarkService(alpha=0.03)
    test_image = create_test_image()
    test_payload = b'img-format-test1'
    
    # Embed watermark
    watermarked = service.embed_watermark(test_image, test_payload)
    
    temp_dir = tempfile.mkdtemp()
    results = {}
    
    try:
        # Test PNG → JPG → PNG
        png_path = os.path.join(temp_dir, "test.png")
        jpg_path = os.path.join(temp_dir, "test.jpg")
        png2_path = os.path.join(temp_dir, "test2.png")
        
        # Save as PNG
        cv2.imwrite(png_path, watermarked)
        
        # Load and save as JPG
        img_png = cv2.imread(png_path)
        cv2.imwrite(jpg_path, img_png, [cv2.IMWRITE_JPEG_QUALITY, 90])
        
        # Load and save back as PNG
        img_jpg = cv2.imread(jpg_path)
        cv2.imwrite(png2_path, img_jpg)
        
        # Load final image
        final_img = cv2.imread(png2_path)
        
        # Try to extract watermark
        extracted = service.extract_watermark(final_img, payload_length=16)
        
        if extracted:
            match_ratio = sum(a == b for a, b in zip(test_payload, extracted)) / len(test_payload)
            success = match_ratio >= 0.5
            results['png_jpg_png'] = {'success': success, 'match': match_ratio}
        else:
            results['png_jpg_png'] = {'success': False, 'match': 0.0}
        
        status = "✓" if results['png_jpg_png']['success'] else "✗"
        print(f"  PNG → JPG → PNG: {status} Match: {results['png_jpg_png']['match']*100:.1f}%")
        
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    success_rate = 1.0 if results['png_jpg_png']['success'] else 0.0
    
    print(f"\nFormat Conversion Success Rate: {success_rate*100:.1f}%")
    
    return results, success_rate


def run_robustness_tests():
    """Run all watermark robustness tests."""
    print("\n" + "="*70)
    print("  WATERMARK ROBUSTNESS TESTING SUITE")
    print("  Phase 8: Comprehensive Validation")
    print("="*70)
    
    all_results = {}
    
    # Test 1: JPEG Compression
    jpeg_results, jpeg_rate = test_jpeg_compression()
    all_results['jpeg'] = {'results': jpeg_results, 'success_rate': jpeg_rate}
    
    # Test 2: Resizing
    resize_results, resize_rate = test_resizing()
    all_results['resize'] = {'results': resize_results, 'success_rate': resize_rate}
    
    # Test 3: Format Conversion
    format_results, format_rate = test_format_conversion()
    all_results['format'] = {'results': format_results, 'success_rate': format_rate}
    
    # Overall Summary
    print("\n" + "="*70)
    print("  ROBUSTNESS TEST SUMMARY")
    print("="*70)
    
    print(f"\nJPEG Compression: {jpeg_rate*100:.1f}% success")
    print(f"Resizing:         {resize_rate*100:.1f}% success")
    print(f"Format Conversion: {format_rate*100:.1f}% success")
    
    overall_rate = (jpeg_rate + resize_rate + format_rate) / 3
    print(f"\nOverall Success Rate: {overall_rate*100:.1f}%")
    print(f"Target: ≥90%")
    
    if overall_rate >= 0.9:
        print("\n✓✓✓ ROBUSTNESS TESTS PASSED (≥90%)")
        return True
    else:
        print(f"\n✗✗✗ ROBUSTNESS TESTS FAILED ({overall_rate*100:.1f}% < 90%)")
        return False


if __name__ == "__main__":
    success = run_robustness_tests()
    sys.exit(0 if success else 1)
