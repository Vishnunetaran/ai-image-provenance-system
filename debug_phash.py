"""Quick debug script to check actual perceptual hash distances."""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent))

from provena_flask.services.phash_service import PerceptualHashService

# Create test image
image = np.zeros((256, 256, 3), dtype=np.uint8)
for i in range(256):
    for j in range(256):
        image[i, j] = [int(255 * i / 256), int(255 * j / 256), 128]

service = PerceptualHashService()

# Original
phash_orig = service.compute_phash(image)
print(f"Original pHash: {phash_orig}")

# Brightness adjusted
image_bright = cv2.convertScaleAbs(image, alpha=1.1, beta=10)
phash_bright = service.compute_phash(image_bright)
dist_bright = service.hamming_distance(phash_orig, phash_bright)
print(f"Brightness adjusted distance: {dist_bright}")

# Resized
image_resized = cv2.resize(image, (200, 200))
image_resized_back = cv2.resize(image_resized, (256, 256))
phash_resized = service.compute_phash(image_resized_back)
dist_resized = service.hamming_distance(phash_orig, phash_resized)
print(f"Resized distance: {dist_resized}")

print("\nRecommended thresholds:")
print(f"  - Similar images (brightness): {dist_bright + 5}")
print(f"  - Resized images: {dist_resized + 5}")
