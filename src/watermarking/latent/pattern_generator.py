"""
Pattern Generator Module for Latent Space Watermarking.

Generates deterministic, cryptographically-secure noise patterns using ChaCha20
stream cipher. These patterns are injected into the diffusion process at specific
timesteps to create imperceptible but robust watermarks.

Key Design Decisions:
    - ChaCha20 provides deterministic CSPRNG output for each (key, image_id) pair
    - Box-Muller transform converts uniform → Gaussian noise (matches diffusion prior)
    - Frequency-domain shaping concentrates energy in JPEG-robust mid-frequency bands
    - Multi-scale patterns support injection at T/2, T/4, T/8 timesteps

References:
    - Tree-Ring Watermarks (Wen et al., 2024)
    - ChaCha20 stream cipher (Bernstein, 2008)
"""

import hashlib
import hmac
import struct
import math
import logging
from typing import Tuple, List, Optional

import numpy as np

try:
    import torch
except ImportError:
    torch = None  # Graceful degradation for environments without PyTorch

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    from cryptography.hazmat.backends import default_backend

    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

CHACHA20_KEY_SIZE = 32  # 256-bit key
CHACHA20_NONCE_SIZE = 16  # 128-bit nonce (ChaCha20 standard)
DEFAULT_FREQ_LOW = 4  # Lower DCT band for mid-frequency emphasis
DEFAULT_FREQ_HIGH = 28  # Upper DCT band for mid-frequency emphasis


# ──────────────────────────────────────────────────────────────────────
# Internal Helpers
# ──────────────────────────────────────────────────────────────────────


def _derive_seed(secret_key: bytes, image_id: str) -> bytes:
    """Derive a deterministic seed from a secret key and image identifier.

    Uses HMAC-SHA256 to combine the key and image_id, producing a 32-byte
    value that is both deterministic and cryptographically bound to the key.

    Args:
        secret_key: 32-byte secret key.
        image_id: Unique identifier for the image.

    Returns:
        32-byte derived seed.

    Raises:
        ValueError: If secret_key is not exactly 32 bytes.
    """
    if len(secret_key) != CHACHA20_KEY_SIZE:
        raise ValueError(
            f"Secret key must be {CHACHA20_KEY_SIZE} bytes, got {len(secret_key)}"
        )
    return hmac.new(secret_key, image_id.encode("utf-8"), hashlib.sha256).digest()


def _chacha20_stream(key: bytes, nonce: bytes, num_bytes: int) -> bytes:
    """Generate a pseudorandom byte stream using ChaCha20.

    Args:
        key: 32-byte ChaCha20 key.
        nonce: 16-byte nonce.
        num_bytes: Number of pseudorandom bytes to generate.

    Returns:
        Pseudorandom byte string of length ``num_bytes``.

    Raises:
        RuntimeError: If the ``cryptography`` package is not available.
    """
    if not _HAS_CRYPTOGRAPHY:
        raise RuntimeError(
            "The 'cryptography' package is required for ChaCha20 pattern "
            "generation. Install it via: pip install cryptography"
        )

    # ChaCha20 in the cryptography library expects a 16-byte nonce
    cipher = Cipher(
        algorithms.ChaCha20(key, nonce),
        mode=None,
        backend=default_backend(),
    )
    encryptor = cipher.encryptor()
    # Encrypt zeros to get the raw keystream
    plaintext = b"\x00" * num_bytes
    return encryptor.update(plaintext) + encryptor.finalize()


def _bytes_to_uniform(raw: bytes) -> np.ndarray:
    """Convert raw bytes to an array of uniform [0, 1) floats.

    Each group of 4 bytes is interpreted as a uint32 and divided by 2**32.

    Args:
        raw: Raw byte string (length must be a multiple of 4).

    Returns:
        1-D numpy float64 array with values in [0, 1).
    """
    n = len(raw) // 4
    uint_values = np.frombuffer(raw[: n * 4], dtype=np.uint32)
    return uint_values.astype(np.float64) / (2**32)


def _box_muller(uniform: np.ndarray) -> np.ndarray:
    """Apply the Box-Muller transform to convert uniform → standard Gaussian.

    Takes pairs of uniform samples and produces pairs of independent standard
    normal samples.  The output length equals the input length (rounded down
    to the nearest even number).

    Args:
        uniform: Array of uniform [0, 1) values.

    Returns:
        Array of standard-normal values (same length, rounded to even).
    """
    n = (len(uniform) // 2) * 2
    u1 = np.clip(uniform[:n:2], 1e-10, 1.0 - 1e-10)
    u2 = uniform[1:n:2]

    r = np.sqrt(-2.0 * np.log(u1))
    theta = 2.0 * math.pi * u2

    z0 = r * np.cos(theta)
    z1 = r * np.sin(theta)

    return np.concatenate([z0, z1])


def _frequency_shape(pattern_2d: np.ndarray, low: int, high: int) -> np.ndarray:
    """Apply frequency-domain shaping to concentrate energy in mid-frequencies.

    Uses DCT to transform the pattern, zeroes out low and very high frequency
    components, and transforms back.  This makes the watermark naturally
    resistant to JPEG compression (which keeps mid-frequencies).

    Args:
        pattern_2d: 2-D numpy array to shape.
        low: Lower DCT coefficient index to preserve.
        high: Upper DCT coefficient index to preserve.

    Returns:
        Frequency-shaped 2-D array normalised to unit energy.
    """
    try:
        from scipy.fft import dctn, idctn
    except ImportError:
        # Fallback: no frequency shaping if scipy unavailable
        logger.warning("scipy not available; skipping frequency shaping")
        norm = np.linalg.norm(pattern_2d)
        return pattern_2d / norm if norm > 0 else pattern_2d

    H, W = pattern_2d.shape
    dct_coeffs = dctn(pattern_2d, type=2, norm="ortho")

    # Build frequency mask: keep mid-frequency bands
    mask = np.zeros_like(dct_coeffs)
    freq_low = min(low, H, W)
    freq_high = min(high, H, W)
    mask[freq_low:freq_high, freq_low:freq_high] = 1.0

    dct_coeffs *= mask
    shaped = idctn(dct_coeffs, type=2, norm="ortho")

    # Normalise to unit energy
    norm = np.linalg.norm(shaped)
    if norm > 0:
        shaped /= norm

    return shaped


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def generate_pattern(
    image_id: str,
    secret_key: bytes,
    shape: Tuple[int, ...],
    freq_low: int = DEFAULT_FREQ_LOW,
    freq_high: int = DEFAULT_FREQ_HIGH,
    apply_freq_shaping: bool = True,
) -> "torch.Tensor":
    """Generate a keyed noise pattern using ChaCha20.

    Produces a deterministic, normally-distributed noise tensor that is unique
    to each (secret_key, image_id) pair.  Optionally applies frequency-domain
    shaping to concentrate energy in JPEG-robust mid-frequency bands.

    Args:
        image_id: Unique identifier for the image.
        secret_key: 32-byte secret key for ChaCha20.
        shape: Desired pattern shape, e.g. ``(C, H, W)`` or ``(H, W)``.
        freq_low: Lower DCT band index for frequency shaping (default 4).
        freq_high: Upper DCT band index for frequency shaping (default 28).
        apply_freq_shaping: Whether to apply frequency-domain shaping.

    Returns:
        Noise pattern tensor of the given ``shape``, normalised to unit energy.

    Raises:
        ValueError: If ``secret_key`` is not 32 bytes.
        RuntimeError: If PyTorch is not installed.

    Example:
        >>> key = b"\\x00" * 32
        >>> pattern = generate_pattern("img-123", key, (4, 64, 64))
        >>> pattern.shape
        torch.Size([4, 64, 64])
    """
    if torch is None:
        raise RuntimeError(
            "PyTorch is required for pattern generation. "
            "Install via: pip install torch"
        )

    # 1. Derive deterministic seed from (key, image_id)
    seed = _derive_seed(secret_key, image_id)

    # 2. Use seed as ChaCha20 key, first 16 bytes as nonce
    chacha_key = seed[:CHACHA20_KEY_SIZE]
    nonce = seed[:CHACHA20_NONCE_SIZE]

    # 3. Generate enough random bytes
    total_elements = 1
    for s in shape:
        total_elements *= s
    # Need 4 bytes per float, 2x for Box-Muller pairs
    num_bytes = total_elements * 4 * 2 + 8  # extra margin

    raw_bytes = _chacha20_stream(chacha_key, nonce, num_bytes)

    # 4. Convert to Gaussian noise
    uniform = _bytes_to_uniform(raw_bytes)
    gaussian = _box_muller(uniform)[:total_elements]

    # 5. Reshape
    pattern_np = gaussian.reshape(shape)

    # 6. Frequency shaping on each 2-D slice
    if apply_freq_shaping and len(shape) >= 2:
        if len(shape) == 2:
            pattern_np = _frequency_shape(pattern_np, freq_low, freq_high)
        elif len(shape) >= 3:
            # Shape (C, H, W) or (B, C, H, W) — shape last two dims
            shaped = np.empty_like(pattern_np)
            it = np.nditer(
                np.empty(shape[:-2]), flags=["multi_index"]
            )
            while not it.finished:
                idx = it.multi_index
                shaped[idx] = _frequency_shape(
                    pattern_np[idx], freq_low, freq_high
                )
                it.iternext()
            pattern_np = shaped

    # 7. Final normalisation
    norm = np.linalg.norm(pattern_np)
    if norm > 0:
        pattern_np = pattern_np / norm

    return torch.tensor(pattern_np, dtype=torch.float32)


def generate_multi_scale_patterns(
    image_id: str,
    secret_key: bytes,
    shape: Tuple[int, ...],
    scales: Optional[List[float]] = None,
    apply_freq_shaping: bool = True,
) -> List["torch.Tensor"]:
    """Generate patterns at multiple scales for multi-timestep injection.

    Creates distinct patterns for each scale by deriving independent sub-keys.
    Used for injecting watermarks at timesteps T/2, T/4, T/8 during diffusion.

    Args:
        image_id: Unique identifier for the image.
        secret_key: 32-byte secret key for ChaCha20.
        shape: Base pattern shape ``(C, H, W)``.
        scales: Scale fractions (default ``[0.5, 0.25, 0.125]``
                corresponding to T/2, T/4, T/8).
        apply_freq_shaping: Whether to apply frequency-domain shaping.

    Returns:
        List of pattern tensors, one per scale, each normalised to unit energy.

    Example:
        >>> key = b"\\x00" * 32
        >>> patterns = generate_multi_scale_patterns("img-123", key, (4, 64, 64))
        >>> len(patterns)
        3
        >>> patterns[0].shape
        torch.Size([4, 64, 64])
    """
    if scales is None:
        scales = [0.5, 0.25, 0.125]

    patterns = []
    for i, scale in enumerate(scales):
        # Derive a unique sub-key for each scale
        sub_key_material = f"scale_{i}_{scale}".encode("utf-8")
        sub_key = hmac.new(
            secret_key, sub_key_material, hashlib.sha256
        ).digest()

        pattern = generate_pattern(
            image_id=image_id,
            secret_key=sub_key,
            shape=shape,
            apply_freq_shaping=apply_freq_shaping,
        )
        patterns.append(pattern)

    return patterns
