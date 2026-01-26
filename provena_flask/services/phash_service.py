"""
Perceptual Hashing Service for Provena-FLASK.

Implements perceptual hashing for tolerant image matching.
Uses pHash (perceptual hash) and dHash (difference hash) algorithms.

IMPORTANT: These are NOT cryptographic hashes (not SHA/MD5).
Perceptual hashes are designed to be similar for visually similar images.
"""

import numpy as np
import cv2
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class PerceptualHashService:
    """
    Perceptual hashing for tolerant image matching.
    
    This service provides:
    - pHash: DCT-based perceptual hash (robust to minor changes)
    - dHash: Difference hash (fast, gradient-based)
    - Hamming distance comparison
    - Configurable similarity threshold
    
    Use cases:
    - Find similar/duplicate images
    - Detect modified versions of original images
    - Tolerant matching despite compression/resizing
    """
    
    # Default hash size
    PHASH_SIZE = 8  # 8x8 = 64-bit hash
    DHASH_SIZE = 8  # 8x8 = 64-bit hash
    
    # Default similarity threshold (Hamming distance)
    DEFAULT_THRESHOLD = 10  # bits different
    
    def __init__(self, threshold: int = DEFAULT_THRESHOLD):
        """
        Initialize perceptual hash service.
        
        Args:
            threshold: Hamming distance threshold for similarity (default: 10)
                      Lower = more strict, Higher = more tolerant
        """
        self.threshold = threshold
        logger.info(f"Perceptual hash service initialized (threshold={threshold})")
    
    def compute_phash(self, image: np.ndarray, hash_size: int = PHASH_SIZE) -> str:
        """
        Compute perceptual hash (pHash) using DCT.
        
        pHash is robust to:
        - Minor color adjustments
        - Slight resizing
        - JPEG compression
        - Gamma correction
        
        Args:
            image: Input image (BGR or grayscale)
            hash_size: Hash dimension (default: 8 for 64-bit hash)
        
        Returns:
            str: Hexadecimal hash string
        
        Example:
            >>> image = cv2.imread('image.png')
            >>> phash = service.compute_phash(image)
            >>> print(phash)  # 'a3f5c8d2e1b4f7a9'
        """
        # Convert to grayscale if needed
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Resize to (hash_size+1) x (hash_size+1)
        # Extra pixel for DCT
        resized = cv2.resize(gray, (hash_size + 1, hash_size + 1), interpolation=cv2.INTER_AREA)
        
        # Convert to float
        resized_float = resized.astype(np.float32)
        
        # Compute DCT (Discrete Cosine Transform)
        dct = cv2.dct(resized_float)
        
        # Extract top-left hash_size x hash_size coefficients
        # (low frequencies, most important for perceptual similarity)
        dct_low = dct[:hash_size, :hash_size]
        
        # Compute median
        median = np.median(dct_low)
        
        # Generate hash: 1 if > median, 0 otherwise
        hash_bits = (dct_low > median).flatten()
        
        # Convert to hex string
        hash_hex = self._bits_to_hex(hash_bits)
        
        logger.debug(f"Computed pHash: {hash_hex}")
        
        return hash_hex
    
    def compute_dhash(self, image: np.ndarray, hash_size: int = DHASH_SIZE) -> str:
        """
        Compute difference hash (dHash) using gradient.
        
        dHash is fast and robust to:
        - Slight resizing
        - Minor color changes
        - JPEG compression
        
        Args:
            image: Input image (BGR or grayscale)
            hash_size: Hash dimension (default: 8 for 64-bit hash)
        
        Returns:
            str: Hexadecimal hash string
        
        Example:
            >>> image = cv2.imread('image.png')
            >>> dhash = service.compute_dhash(image)
            >>> print(dhash)  # 'b2e4a1c7f3d8e5a2'
        """
        # Convert to grayscale if needed
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Resize to (hash_size+1) x hash_size
        # Extra column for gradient computation
        resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
        
        # Compute horizontal gradient
        # Compare each pixel with its right neighbor
        diff = resized[:, 1:] > resized[:, :-1]
        
        # Flatten to 1D array of bits
        hash_bits = diff.flatten()
        
        # Convert to hex string
        hash_hex = self._bits_to_hex(hash_bits)
        
        logger.debug(f"Computed dHash: {hash_hex}")
        
        return hash_hex
    
    def compute_ahash(self, image: np.ndarray, hash_size: int = 8) -> str:
        """
        Compute average hash (aHash) - simplest method.
        
        aHash is very fast but less robust than pHash/dHash.
        
        Args:
            image: Input image (BGR or grayscale)
            hash_size: Hash dimension (default: 8 for 64-bit hash)
        
        Returns:
            str: Hexadecimal hash string
        """
        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Resize to hash_size x hash_size
        resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
        
        # Compute mean
        mean = np.mean(resized)
        
        # Generate hash: 1 if > mean, 0 otherwise
        hash_bits = (resized > mean).flatten()
        
        # Convert to hex
        hash_hex = self._bits_to_hex(hash_bits)
        
        logger.debug(f"Computed aHash: {hash_hex}")
        
        return hash_hex
    
    def hamming_distance(self, hash1: str, hash2: str) -> int:
        """
        Calculate Hamming distance between two hashes.
        
        Hamming distance = number of differing bits.
        Lower distance = more similar images.
        
        Args:
            hash1: First hash (hex string)
            hash2: Second hash (hex string)
        
        Returns:
            int: Hamming distance (0 = identical, 64 = completely different for 64-bit hash)
        
        Example:
            >>> dist = service.hamming_distance(hash1, hash2)
            >>> print(f"Distance: {dist} bits")
        """
        if len(hash1) != len(hash2):
            raise ValueError(f"Hash lengths must match: {len(hash1)} != {len(hash2)}")
        
        # Convert hex to integers
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        
        # XOR to find differing bits
        xor = int1 ^ int2
        
        # Count set bits (Hamming distance)
        distance = bin(xor).count('1')
        
        return distance
    
    def are_similar(self, hash1: str, hash2: str, threshold: Optional[int] = None) -> bool:
        """
        Check if two hashes represent similar images.
        
        Args:
            hash1: First hash
            hash2: Second hash
            threshold: Optional custom threshold (uses default if None)
        
        Returns:
            bool: True if similar (distance ≤ threshold)
        
        Example:
            >>> similar = service.are_similar(hash1, hash2)
            >>> print(f"Similar: {similar}")
        """
        if threshold is None:
            threshold = self.threshold
        
        distance = self.hamming_distance(hash1, hash2)
        
        is_similar = distance <= threshold
        
        logger.debug(f"Similarity check: distance={distance}, threshold={threshold}, similar={is_similar}")
        
        return is_similar
    
    def compare_images(self, image1: np.ndarray, image2: np.ndarray, 
                      method: str = 'phash') -> Tuple[int, bool]:
        """
        Compare two images using perceptual hashing.
        
        Args:
            image1: First image
            image2: Second image
            method: Hash method ('phash', 'dhash', or 'ahash')
        
        Returns:
            Tuple[int, bool]: (hamming_distance, is_similar)
        
        Example:
            >>> dist, similar = service.compare_images(img1, img2, method='phash')
            >>> print(f"Distance: {dist}, Similar: {similar}")
        """
        # Compute hashes
        if method == 'phash':
            hash1 = self.compute_phash(image1)
            hash2 = self.compute_phash(image2)
        elif method == 'dhash':
            hash1 = self.compute_dhash(image1)
            hash2 = self.compute_dhash(image2)
        elif method == 'ahash':
            hash1 = self.compute_ahash(image1)
            hash2 = self.compute_ahash(image2)
        else:
            raise ValueError(f"Unknown method: {method}. Use 'phash', 'dhash', or 'ahash'")
        
        # Calculate distance
        distance = self.hamming_distance(hash1, hash2)
        is_similar = distance <= self.threshold
        
        logger.info(f"Image comparison ({method}): distance={distance}, similar={is_similar}")
        
        return distance, is_similar
    
    def _bits_to_hex(self, bits: np.ndarray) -> str:
        """
        Convert bit array to hexadecimal string.
        
        Args:
            bits: Boolean array of bits
        
        Returns:
            str: Hexadecimal string
        """
        # Convert to list of ints
        bit_list = [int(b) for b in bits]
        
        # Pad to multiple of 4 (for hex conversion)
        while len(bit_list) % 4 != 0:
            bit_list.append(0)
        
        # Convert to hex
        hex_string = ''
        for i in range(0, len(bit_list), 4):
            nibble = bit_list[i:i+4]
            value = nibble[0] * 8 + nibble[1] * 4 + nibble[2] * 2 + nibble[3]
            hex_string += format(value, 'x')
        
        return hex_string
    
    def get_hash_info(self, hash_hex: str) -> dict:
        """
        Get information about a hash.
        
        Args:
            hash_hex: Hexadecimal hash string
        
        Returns:
            dict: Hash information (length, bit_count, etc.)
        """
        bit_count = len(hash_hex) * 4  # Each hex char = 4 bits
        
        return {
            'hash': hash_hex,
            'length': len(hash_hex),
            'bit_count': bit_count,
            'type': 'perceptual_hash'
        }


# Convenience functions
def compute_phash(image: np.ndarray) -> str:
    """Compute pHash. See PerceptualHashService.compute_phash() for details."""
    service = PerceptualHashService()
    return service.compute_phash(image)


def compute_dhash(image: np.ndarray) -> str:
    """Compute dHash. See PerceptualHashService.compute_dhash() for details."""
    service = PerceptualHashService()
    return service.compute_dhash(image)


def compare_hashes(hash1: str, hash2: str, threshold: int = 10) -> Tuple[int, bool]:
    """
    Compare two hashes.
    
    Returns:
        Tuple[int, bool]: (hamming_distance, is_similar)
    """
    service = PerceptualHashService(threshold=threshold)
    distance = service.hamming_distance(hash1, hash2)
    is_similar = service.are_similar(hash1, hash2, threshold)
    return distance, is_similar
