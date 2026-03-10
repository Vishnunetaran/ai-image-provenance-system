"""
PROVENA Trinity - Latent Space Watermarking Module.

Implements Tree-Ring style watermarking (Wen et al., 2024) that embeds
watermarks during the diffusion process itself, achieving 90%+ detection
rates even after heavy image transformations.

Public API:
    - generate_pattern: ChaCha20-based deterministic noise pattern generation
    - encode_payload / decode_payload: LDPC-coded 160-bit payload handling
    - LatentDiffusionInjector: Hook into diffusion pipeline for embedding
    - LatentWatermarkExtractor: Extract and verify watermarks from images
"""

from src.watermarking.latent.pattern_generator import (
    generate_pattern,
    generate_multi_scale_patterns,
)
from src.watermarking.latent.payload import encode_payload, decode_payload
from src.watermarking.latent.diffusion_injector import LatentDiffusionInjector
from src.watermarking.latent.extractor import LatentWatermarkExtractor

__all__ = [
    "generate_pattern",
    "generate_multi_scale_patterns",
    "encode_payload",
    "decode_payload",
    "LatentDiffusionInjector",
    "LatentWatermarkExtractor",
]
