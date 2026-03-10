"""
Diffusion Injector Module for Latent Space Watermarking.

Hooks into the Stable Diffusion reverse diffusion process to inject
Tree-Ring style watermark patterns at specific timesteps.  This makes
the watermark a fundamental part of image generation rather than a
post-processing add-on.

Injection Strategy (from PRD):
    - Timestep T/2:  Strongest injection (early in denoising, most persistent)
    - Timestep T/4:  Medium injection (mid-denoising reinforcement)
    - Timestep T/8:  Light injection (late-stage fine detail preservation)

The injector also supports Tree-Ring's initial-noise approach:
embed a ring pattern in the Fourier domain of the starting noise tensor,
which propagates through the entire diffusion process.

References:
    - Tree-Ring Watermarks (Wen et al., 2024)
    - Stable Signature (Fernandez et al., Meta 2023)
"""

import math
import logging
from typing import Tuple, List, Optional, Callable, Dict, Any

import numpy as np

try:
    import torch
    import torch.nn.functional as F
except ImportError:
    torch = None
    F = None

from src.watermarking.latent.pattern_generator import (
    generate_pattern,
    generate_multi_scale_patterns,
)
from src.watermarking.latent.payload import encode_payload, get_coded_length

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

DEFAULT_ALPHA = 0.08  # Embedding strength
DEFAULT_TIMESTEP_FRACTIONS = [0.5, 0.25, 0.125]  # T/2, T/4, T/8
RING_RADIUS_MIN = 5  # Minimum ring radius in Fourier space
RING_RADIUS_MAX = 20  # Maximum ring radius in Fourier space


class LatentDiffusionInjector:
    """Inject watermark patterns into the diffusion process.

    This class provides mechanisms to embed provenance watermarks during
    image generation with diffusion models (e.g. Stable Diffusion).

    Two injection modes are supported:

    1. **Timestep injection**: Add keyed noise patterns at specific denoising
       timesteps (T/2, T/4, T/8) during the reverse diffusion process.

    2. **Initial noise injection** (Tree-Ring style): Embed a ring pattern
       in the Fourier domain of the starting noise, which naturally propagates
       through the entire denoising process.

    Attributes:
        secret_key: 32-byte secret key for pattern generation.
        alpha: Embedding strength (higher = more robust but less invisible).
        timestep_fractions: Which fractions of total timesteps to inject at.

    Example:
        >>> injector = LatentDiffusionInjector(secret_key=b"\\x00" * 32)
        >>> # Mode 1: Timestep injection callback
        >>> callback = injector.create_pipeline_callback("img-001")
        >>> # Mode 2: Initial noise injection
        >>> noise = torch.randn(1, 4, 64, 64)
        >>> watermarked_noise = injector.watermark_initial_noise(noise, "img-001")
    """

    def __init__(
        self,
        secret_key: bytes,
        alpha: float = DEFAULT_ALPHA,
        timestep_fractions: Optional[List[float]] = None,
        adaptive_strength: bool = True,
    ):
        """Initialise the diffusion injector.

        Args:
            secret_key: 32-byte secret key for ChaCha20 pattern generation.
            alpha: Base embedding strength (0.01–0.2, default 0.08).
            timestep_fractions: Fractions of total timesteps at which to
                inject watermark patterns.  Default: [0.5, 0.25, 0.125].
            adaptive_strength: If True, adapt alpha based on local variance
                of the latent tensor (higher in textured regions).

        Raises:
            ValueError: If secret_key is not 32 bytes or alpha is out of range.
        """
        if len(secret_key) != 32:
            raise ValueError(f"Secret key must be 32 bytes, got {len(secret_key)}")
        if not 0.001 <= alpha <= 1.0:
            raise ValueError(f"Alpha must be in [0.001, 1.0], got {alpha}")

        self.secret_key = secret_key
        self.alpha = alpha
        self.timestep_fractions = timestep_fractions or DEFAULT_TIMESTEP_FRACTIONS
        self.adaptive_strength = adaptive_strength

        # Cache for generated patterns (avoid regenerating each step)
        self._pattern_cache: Dict[str, List["torch.Tensor"]] = {}

        logger.info(
            f"LatentDiffusionInjector initialised: alpha={alpha}, "
            f"timesteps={self.timestep_fractions}, adaptive={adaptive_strength}"
        )

    def _get_patterns(
        self, image_id: str, shape: Tuple[int, ...]
    ) -> List["torch.Tensor"]:
        """Get or generate cached multi-scale patterns for an image.

        Args:
            image_id: Unique image identifier.
            shape: Latent tensor shape (C, H, W) or (B, C, H, W).

        Returns:
            List of pattern tensors, one per timestep fraction.
        """
        cache_key = f"{image_id}_{shape}"
        if cache_key not in self._pattern_cache:
            # Use spatial dims for pattern generation
            if len(shape) == 4:
                pattern_shape = shape[1:]  # (C, H, W)
            else:
                pattern_shape = shape

            self._pattern_cache[cache_key] = generate_multi_scale_patterns(
                image_id=image_id,
                secret_key=self.secret_key,
                shape=pattern_shape,
                scales=self.timestep_fractions,
            )
        return self._pattern_cache[cache_key]

    def _compute_adaptive_alpha(
        self, latent: "torch.Tensor", base_alpha: float
    ) -> "torch.Tensor":
        """Compute spatially-adaptive embedding strength.

        Higher strength in textured regions (high local variance), lower
        in smooth regions where artifacts would be visible.

        Args:
            latent: Latent tensor (B, C, H, W) or (C, H, W).
            base_alpha: Base embedding strength.

        Returns:
            Alpha map of same spatial dimensions as latent.
        """
        if not self.adaptive_strength:
            return base_alpha

        if torch is None:
            return base_alpha

        # Compute local variance using 5x5 sliding window
        if latent.dim() == 3:
            lat = latent.unsqueeze(0)
        else:
            lat = latent

        # Average across channels
        mean_channel = lat.mean(dim=1, keepdim=True)  # (B, 1, H, W)

        # Local mean via average pooling
        kernel_size = 5
        padding = kernel_size // 2
        local_mean = F.avg_pool2d(
            mean_channel, kernel_size, stride=1, padding=padding
        )

        # Local variance
        local_var = F.avg_pool2d(
            (mean_channel - local_mean) ** 2, kernel_size, stride=1, padding=padding
        )

        # Normalise variance to [0.5, 1.5] range → alpha multiplier
        var_norm = local_var / (local_var.mean() + 1e-8)
        alpha_map = base_alpha * (0.5 + torch.clamp(var_norm, 0, 2.0) / 2.0)

        if latent.dim() == 3:
            alpha_map = alpha_map.squeeze(0)

        return alpha_map

    def inject(
        self,
        latent_noise: "torch.Tensor",
        timestep: int,
        total_timesteps: int,
        image_id: str,
    ) -> "torch.Tensor":
        """Inject watermark pattern at a specific denoising timestep.

        Checks whether the current timestep matches one of the target
        fractions.  If so, adds the corresponding keyed pattern to the
        latent tensor.

        Args:
            latent_noise: Current latent tensor from the diffusion process.
            timestep: Current denoising timestep (counting down from T).
            total_timesteps: Total number of timesteps in the schedule.
            image_id: Unique image identifier for pattern generation.

        Returns:
            Modified latent tensor (same shape as input).  If the current
            timestep does not match a target, the tensor is returned unchanged.

        Example:
            >>> injector = LatentDiffusionInjector(secret_key=b"\\x00" * 32)
            >>> latent = torch.randn(1, 4, 64, 64)
            >>> modified = injector.inject(latent, timestep=500, total_timesteps=1000, image_id="img-001")
            >>> modified.shape
            torch.Size([1, 4, 64, 64])
        """
        if torch is None:
            raise RuntimeError("PyTorch required for latent injection")

        # Determine which fraction this timestep corresponds to
        fraction = timestep / total_timesteps
        matched_index = None

        for i, target_frac in enumerate(self.timestep_fractions):
            # Allow ±2% tolerance for timestep matching
            if abs(fraction - target_frac) < 0.02:
                matched_index = i
                break

        if matched_index is None:
            return latent_noise  # Not a target timestep

        # Get pattern for this scale
        patterns = self._get_patterns(image_id, latent_noise.shape)
        pattern = patterns[matched_index]

        # Expand pattern to batch dimension if needed
        if latent_noise.dim() == 4 and pattern.dim() == 3:
            pattern = pattern.unsqueeze(0).expand_as(latent_noise)

        pattern = pattern.to(latent_noise.device, latent_noise.dtype)

        # Compute adaptive alpha
        alpha = self._compute_adaptive_alpha(latent_noise, self.alpha)

        # Scale alpha by timestep importance (earlier = stronger)
        timestep_weight = 1.0 - (1.0 - fraction) * 0.5  # [0.5, 1.0]

        # Inject
        modified = latent_noise + alpha * timestep_weight * pattern

        logger.debug(
            f"Watermark injected at timestep {timestep}/{total_timesteps} "
            f"(fraction={fraction:.3f}, scale_index={matched_index}, "
            f"weight={timestep_weight:.3f})"
        )

        return modified

    def watermark_initial_noise(
        self,
        noise: "torch.Tensor",
        image_id: str,
        ring_radius_min: int = RING_RADIUS_MIN,
        ring_radius_max: int = RING_RADIUS_MAX,
    ) -> "torch.Tensor":
        """Embed a Tree-Ring watermark in the initial noise tensor.

        Operates in the Fourier domain: places a keyed pattern on a ring
        of specific radii in the 2-D FFT of the noise.  Since the diffusion
        process preserves frequency-domain structure, this watermark
        survives the entire denoising pipeline.

        Args:
            noise: Initial noise tensor (B, C, H, W) or (C, H, W).
            image_id: Unique image identifier.
            ring_radius_min: Inner ring radius in frequency space.
            ring_radius_max: Outer ring radius in frequency space.

        Returns:
            Watermarked noise tensor (same shape as input).

        Example:
            >>> injector = LatentDiffusionInjector(secret_key=b"\\x00" * 32)
            >>> noise = torch.randn(1, 4, 64, 64)
            >>> wm_noise = injector.watermark_initial_noise(noise, "img-001")
            >>> wm_noise.shape == noise.shape
            True
        """
        if torch is None:
            raise RuntimeError("PyTorch required for Tree-Ring injection")

        had_batch = noise.dim() == 4
        if noise.dim() == 3:
            noise = noise.unsqueeze(0)

        B, C, H, W = noise.shape
        result = noise.clone()

        # Generate keyed pattern in Fourier space
        pattern = generate_pattern(
            image_id=image_id,
            secret_key=self.secret_key,
            shape=(C, H, W),
            apply_freq_shaping=False,  # We shape manually via ring mask
        ).to(noise.device, noise.dtype)

        # Create ring mask in frequency domain
        cy, cx = H // 2, W // 2
        y_coords = torch.arange(H, device=noise.device).float() - cy
        x_coords = torch.arange(W, device=noise.device).float() - cx
        yy, xx = torch.meshgrid(y_coords, x_coords, indexing="ij")
        radius = torch.sqrt(xx**2 + yy**2)

        ring_mask = ((radius >= ring_radius_min) & (radius <= ring_radius_max)).float()
        ring_mask = ring_mask.unsqueeze(0)  # (1, H, W)

        for b in range(B):
            for c in range(C):
                # FFT of current noise channel
                noise_fft = torch.fft.fft2(noise[b, c])
                noise_fft_shifted = torch.fft.fftshift(noise_fft)

                # Pattern in frequency domain
                pattern_fft = torch.fft.fft2(pattern[c])
                pattern_fft_shifted = torch.fft.fftshift(pattern_fft)

                # Replace ring region with keyed pattern
                modified_fft = noise_fft_shifted.clone()
                mask = ring_mask[0]
                modified_fft = (
                    modified_fft * (1 - mask)
                    + pattern_fft_shifted * mask * self.alpha * 5.0
                )

                # Inverse FFT
                modified_fft = torch.fft.ifftshift(modified_fft)
                result[b, c] = torch.fft.ifft2(modified_fft).real

        if not had_batch:
            result = result.squeeze(0)

        logger.info(
            f"Tree-Ring watermark embedded in initial noise: "
            f"rings=[{ring_radius_min}, {ring_radius_max}], "
            f"shape={noise.shape}"
        )

        return result

    def create_pipeline_callback(
        self,
        image_id: str,
    ) -> Callable:
        """Create a callback function compatible with HuggingFace diffusers.

        The returned callback can be passed to
        ``StableDiffusionPipeline.__call__`` via the ``callback_on_step_end``
        parameter.

        Args:
            image_id: Unique image identifier for watermark generation.

        Returns:
            Callback function with signature
            ``(pipe, step_index, timestep, callback_kwargs) -> callback_kwargs``.

        Example:
            >>> injector = LatentDiffusionInjector(secret_key=b"\\x00" * 32)
            >>> callback = injector.create_pipeline_callback("img-001")
            >>> # pipe = StableDiffusionPipeline.from_pretrained(...)
            >>> # result = pipe(prompt, callback_on_step_end=callback)
        """

        def step_callback(
            pipe: Any,
            step_index: int,
            timestep: "torch.Tensor",
            callback_kwargs: Dict[str, Any],
        ) -> Dict[str, Any]:
            """Diffusers pipeline step callback for watermark injection."""
            latents = callback_kwargs.get("latents")
            if latents is None:
                return callback_kwargs

            # Get total timesteps from scheduler
            total_steps = len(pipe.scheduler.timesteps)
            current_timestep = int(timestep)
            total_timesteps = int(pipe.scheduler.config.num_train_timesteps)

            # Inject watermark pattern
            modified_latents = self.inject(
                latent_noise=latents,
                timestep=current_timestep,
                total_timesteps=total_timesteps,
                image_id=image_id,
            )

            callback_kwargs["latents"] = modified_latents
            return callback_kwargs

        return step_callback

    def clear_cache(self) -> None:
        """Clear the pattern cache to free memory."""
        self._pattern_cache.clear()
        logger.debug("Pattern cache cleared")
