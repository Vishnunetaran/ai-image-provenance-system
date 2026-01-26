"""
Comprehensive test suite for perceptual hashing.

Tests pHash, dHash, and similarity detection.
"""

import os
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.phash_service import PerceptualHashService


def create_test_image(size=(256, 256), pattern='gradient') -> np.ndarray:
    """Create test images with different patterns."""
    image = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    
    if pattern == 'gradient':
        for i in range(size[0]):
            for j in range(size[1]):
                image[i, j] = [
                    int(255 * i / size[0]),
                    int(255 * j / size[1]),
                    128
                ]
    elif pattern == 'checkerboard':
        block_size = 32
        for i in range(size[0]):
            for j in range(size[1]):
                if ((i // block_size) + (j // block_size)) % 2 == 0:
                    image[i, j] = [255, 255, 255]
                else:
                    image[i, j] = [0, 0, 0]
    elif pattern == 'circles':
        center = (size[0] // 2, size[1] // 2)
        for i in range(size[0]):
            for j in range(size[1]):
                dist = np.sqrt((i - center[0])**2 + (j - center[1])**2)
                value = int(128 + 127 * np.sin(dist / 10))
                image[i, j] = [value, value, value]
    
    return image


def run_tests():
    """Run all perceptual hashing tests."""
    print("\n" + "="*70)
    print("  PROVENA-FLASK PERCEPTUAL HASHING TEST SUITE")
    print("  Phase 4: pHash and dHash Implementation")
    print("="*70)
    
    service = PerceptualHashService(threshold=10)
    
    try:
        # Test 1: pHash computation
        print("\n[1/8] Testing pHash computation...")
        image1 = create_test_image(pattern='gradient')
        phash1 = service.compute_phash(image1)
        
        assert isinstance(phash1, str), "pHash should be string"
        assert len(phash1) == 16, f"pHash should be 16 hex chars (64 bits), got {len(phash1)}"
        assert all(c in '0123456789abcdef' for c in phash1), "pHash should be hex"
        
        print(f"   pHash: {phash1}")
        print("✓ PASS: pHash computed successfully")
        
        # Test 2: dHash computation
        print("\n[2/8] Testing dHash computation...")
        dhash1 = service.compute_dhash(image1)
        
        assert isinstance(dhash1, str), "dHash should be string"
        assert len(dhash1) == 16, f"dHash should be 16 hex chars, got {len(dhash1)}"
        
        print(f"   dHash: {dhash1}")
        print("✓ PASS: dHash computed successfully")
        
        # Test 3: aHash computation
        print("\n[3/8] Testing aHash computation...")
        ahash1 = service.compute_ahash(image1)
        
        assert isinstance(ahash1, str), "aHash should be string"
        assert len(ahash1) == 16, f"aHash should be 16 hex chars, got {len(ahash1)}"
        
        print(f"   aHash: {ahash1}")
        print("✓ PASS: aHash computed successfully")
        
        # Test 4: Identical images have distance 0
        print("\n[4/8] Testing identical images...")
        phash1_copy = service.compute_phash(image1.copy())
        distance_identical = service.hamming_distance(phash1, phash1_copy)
        
        assert distance_identical == 0, f"Identical images should have distance 0, got {distance_identical}"
        
        print(f"   Hamming distance: {distance_identical}")
        print("✓ PASS: Identical images have distance 0")
        
        # Test 5: Similar images (slight modification)
        print("\n[5/8] Testing similar images (brightness adjustment)...")
        image1_bright = cv2.convertScaleAbs(image1, alpha=1.1, beta=10)
        phash1_bright = service.compute_phash(image1_bright)
        distance_similar = service.hamming_distance(phash1, phash1_bright)
        
        print(f"   Hamming distance: {distance_similar}")
        assert distance_similar < 30, f"Similar images should have reasonable distance, got {distance_similar}"
        
        is_similar = service.are_similar(phash1, phash1_bright, threshold=30)
        print(f"   Are similar (threshold=30): {is_similar}")
        print("✓ PASS: Similar images detected")
        
        # Test 6: Different images have high distance
        print("\n[6/8] Testing different images...")
        image2 = create_test_image(pattern='checkerboard')
        phash2 = service.compute_phash(image2)
        distance_different = service.hamming_distance(phash1, phash2)
        
        print(f"   Hamming distance: {distance_different}")
        assert distance_different > 20, f"Different images should have high distance, got {distance_different}"
        
        is_different = not service.are_similar(phash1, phash2)
        print(f"   Are different: {is_different}")
        print("✓ PASS: Different images detected")
        
        # Test 7: Resize robustness
        print("\n[7/8] Testing resize robustness...")
        image1_resized = cv2.resize(image1, (200, 200))
        image1_resized_back = cv2.resize(image1_resized, (256, 256))
        phash1_resized = service.compute_phash(image1_resized_back)
        distance_resized = service.hamming_distance(phash1, phash1_resized)
        
        print(f"   Hamming distance after resize: {distance_resized}")
        assert distance_resized < 32, f"Resized image should be reasonably similar, got distance {distance_resized}"
        print("✓ PASS: pHash robust to resizing")
        
        # Test 8: Compare images method
        print("\n[8/8] Testing compare_images method...")
        dist_phash, similar_phash = service.compare_images(image1, image1_bright, method='phash')
        dist_dhash, similar_dhash = service.compare_images(image1, image1_bright, method='dhash')
        
        print(f"   pHash comparison: distance={dist_phash}, similar={similar_phash}")
        print(f"   dHash comparison: distance={dist_dhash}, similar={similar_dhash}")
        
        assert similar_phash or similar_dhash, "At least one method should detect similarity"
        print("✓ PASS: Image comparison works")
        
        print("\n" + "="*70)
        print("  ✓✓✓ ALL PERCEPTUAL HASHING TESTS PASSED ✓✓✓")
        print("="*70)
        print("\nPhase 4 Perceptual Hashing is VERIFIED and OPERATIONAL")
        print("\nKey Results:")
        print(f"  - pHash: {len(phash1)*4}-bit hash")
        print(f"  - dHash: {len(dhash1)*4}-bit hash")
        print(f"  - aHash: {len(ahash1)*4}-bit hash")
        print(f"  - Identical images: distance = 0")
        print(f"  - Similar images: distance = {distance_similar}")
        print(f"  - Different images: distance = {distance_different}")
        print(f"  - Resize robust: distance = {distance_resized}")
        
        return True
        
    except AssertionError as e:
        print(f"\n✗ FAIL: {str(e)}")
        return False
    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
