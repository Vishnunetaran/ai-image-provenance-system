"""
CNN + ViT Hybrid Watermark Encoder.

Embeds a binary watermark payload into an image using a learned residual
approach.  The encoder combines a CNN backbone (ResNet-50) for local feature
extraction with a small Vision Transformer for global context, producing
an imperceptible residual that is added to the original image.

Architecture:
    Input: (image [B,3,H,W], watermark [B, payload_bits])
      → ResNet-50 feature extractor (frozen or fine-tuned)
      → Watermark projection to spatial feature map
      → ViT cross-attention: image features × watermark features
      → Upsampling decoder → residual image [B,3,H,W]
    Output: watermarked = image + residual

References:
    - StegaStamp (Tancik et al., 2020)
    - Stable Signature (Fernandez et al., Meta 2023)
    - HiDDeN (Zhu et al., 2018)
"""

import math
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError:
    raise ImportError("PyTorch is required: pip install torch")

try:
    import timm
except ImportError:
    timm = None
    logger.warning("timm not available; using basic CNN backbone")


# ──────────────────────────────────────────────────────────────────────
# Helper Modules
# ──────────────────────────────────────────────────────────────────────


class WatermarkProjection(nn.Module):
    """Project binary watermark bits into a spatial feature map.

    Takes a 1-D bit vector and projects it into a 2-D feature map
    that can be combined with image features via cross-attention.
    """

    def __init__(self, payload_bits: int, feature_dim: int, spatial_size: int):
        super().__init__()
        self.spatial_size = spatial_size
        self.projection = nn.Sequential(
            nn.Linear(payload_bits, feature_dim * 2),
            nn.GELU(),
            nn.Linear(feature_dim * 2, feature_dim * spatial_size * spatial_size),
        )
        self.norm = nn.LayerNorm(feature_dim)

    def forward(self, watermark: torch.Tensor) -> torch.Tensor:
        """Project watermark to spatial features.

        Args:
            watermark: [B, payload_bits] binary tensor.

        Returns:
            [B, feature_dim, S, S] spatial feature map.
        """
        B = watermark.shape[0]
        x = self.projection(watermark.float())
        x = x.view(B, -1, self.spatial_size, self.spatial_size)
        # Apply layer norm per-spatial-position
        x = x.permute(0, 2, 3, 1)  # [B, S, S, C]
        x = self.norm(x)
        x = x.permute(0, 3, 1, 2)  # [B, C, S, S]
        return x


class CrossAttentionBlock(nn.Module):
    """Cross-attention between image features and watermark features.

    Uses multi-head attention where queries come from image features
    and keys/values come from watermark features.
    """

    def __init__(self, dim: int, num_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        self.attention = nn.MultiheadAttention(
            embed_dim=dim, num_heads=num_heads, dropout=dropout, batch_first=True
        )
        self.norm1 = nn.LayerNorm(dim)
        self.norm2 = nn.LayerNorm(dim)
        self.ffn = nn.Sequential(
            nn.Linear(dim, dim * 4),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim * 4, dim),
            nn.Dropout(dropout),
        )

    def forward(
        self, image_tokens: torch.Tensor, wm_tokens: torch.Tensor
    ) -> torch.Tensor:
        """Apply cross-attention.

        Args:
            image_tokens: [B, N, D] image feature tokens.
            wm_tokens: [B, M, D] watermark feature tokens.

        Returns:
            [B, N, D] attended image tokens.
        """
        # Cross-attention: Q = image, K/V = watermark
        attended, _ = self.attention(
            query=self.norm1(image_tokens),
            key=wm_tokens,
            value=wm_tokens,
        )
        x = image_tokens + attended
        x = x + self.ffn(self.norm2(x))
        return x


class UpsampleBlock(nn.Module):
    """Upsampling block with skip connection support."""

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.up = nn.Sequential(
            nn.ConvTranspose2d(in_channels, out_channels, 4, stride=2, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.GELU(),
            nn.Conv2d(out_channels, out_channels, 3, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.GELU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.up(x)


# ──────────────────────────────────────────────────────────────────────
# Main Encoder
# ──────────────────────────────────────────────────────────────────────


class WatermarkEncoder(nn.Module):
    """CNN + ViT Hybrid Watermark Encoder.

    Embeds a binary watermark payload into an image by computing a
    learned residual.  The output watermarked image is:
        ``watermarked = image + scale * tanh(residual)``

    where ``scale`` controls the maximum residual magnitude (ensuring
    imperceptibility).

    Args:
        payload_bits: Number of watermark bits to embed (default 160).
        image_size: Expected input image size (default 256).
        feature_dim: Internal feature dimension (default 256).
        num_cross_attn_layers: Number of cross-attention layers (default 4).
        residual_scale: Maximum residual magnitude in [0, 255] (default 3.0).
        use_pretrained_backbone: Use pretrained ResNet-50 weights.

    Example:
        >>> encoder = WatermarkEncoder(payload_bits=160, image_size=256)
        >>> image = torch.randn(2, 3, 256, 256)
        >>> watermark = torch.randint(0, 2, (2, 160)).float()
        >>> watermarked = encoder(image, watermark)
        >>> watermarked.shape
        torch.Size([2, 3, 256, 256])
    """

    def __init__(
        self,
        payload_bits: int = 160,
        image_size: int = 256,
        feature_dim: int = 256,
        num_cross_attn_layers: int = 4,
        residual_scale: float = 3.0,
        use_pretrained_backbone: bool = True,
    ):
        super().__init__()

        self.payload_bits = payload_bits
        self.image_size = image_size
        self.feature_dim = feature_dim
        self.residual_scale = residual_scale / 255.0  # Normalise to [0, 1]

        # ── CNN Backbone ──────────────────────────────────────────
        if timm is not None and use_pretrained_backbone:
            self.backbone = timm.create_model(
                "resnet50",
                pretrained=True,
                features_only=True,
                out_indices=(2, 3),  # layer2 (512ch) and layer3 (1024ch)
            )
            backbone_channels = [512, 1024]
        else:
            # Lightweight fallback backbone
            self.backbone = nn.Sequential(
                nn.Conv2d(3, 64, 7, stride=2, padding=3),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.MaxPool2d(3, stride=2, padding=1),
                nn.Conv2d(64, 128, 3, padding=1),
                nn.BatchNorm2d(128),
                nn.ReLU(),
                nn.Conv2d(128, 256, 3, stride=2, padding=1),
                nn.BatchNorm2d(256),
                nn.ReLU(),
                nn.Conv2d(256, feature_dim, 3, stride=2, padding=1),
                nn.BatchNorm2d(feature_dim),
                nn.ReLU(),
            )
            backbone_channels = [feature_dim]

        # ── Feature Projection ────────────────────────────────────
        self.feature_proj = nn.Conv2d(
            backbone_channels[-1], feature_dim, 1
        )

        # ── Watermark Projection ──────────────────────────────────
        spatial_size = image_size // 16  # After 4x downsampling
        self.wm_projection = WatermarkProjection(
            payload_bits, feature_dim, spatial_size
        )

        # ── Cross-Attention Layers ────────────────────────────────
        self.cross_attention_layers = nn.ModuleList(
            [
                CrossAttentionBlock(feature_dim, num_heads=4)
                for _ in range(num_cross_attn_layers)
            ]
        )

        # ── Upsampling Decoder ────────────────────────────────────
        self.decoder = nn.Sequential(
            UpsampleBlock(feature_dim, 128),   # 16x → 32x
            UpsampleBlock(128, 64),             # 32x → 64x
            UpsampleBlock(64, 32),              # 64x → 128x
            UpsampleBlock(32, 16),              # 128x → 256x
            nn.Conv2d(16, 3, 3, padding=1),     # Final residual
        )

        self._init_weights()

    def _init_weights(self):
        """Initialise decoder weights for near-zero initial residual."""
        for m in self.decoder.modules():
            if isinstance(m, (nn.Conv2d, nn.ConvTranspose2d)):
                nn.init.kaiming_normal_(m.weight, mode="fan_out")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)

        # Zero-init the final conv for minimal initial perturbation
        final_conv = self.decoder[-1]
        nn.init.zeros_(final_conv.weight)
        if final_conv.bias is not None:
            nn.init.zeros_(final_conv.bias)

    def forward(
        self,
        image: torch.Tensor,
        watermark: torch.Tensor,
    ) -> torch.Tensor:
        """Embed watermark into image.

        Args:
            image: Input images [B, 3, H, W] normalised to [0, 1].
            watermark: Binary watermark payload [B, payload_bits].

        Returns:
            Watermarked images [B, 3, H, W] in same range as input.
        """
        B = image.shape[0]

        # 1. Extract CNN features
        if timm is not None and hasattr(self.backbone, "feature_info"):
            features = self.backbone(image)
            img_features = self.feature_proj(features[-1])  # Use deepest features
        else:
            img_features = self.backbone(image)

        # 2. Project watermark to spatial features
        wm_features = self.wm_projection(watermark)  # [B, D, S, S]

        # 3. Reshape for transformer: (B, C, H, W) → (B, H*W, C)
        _, C, H, W = img_features.shape
        img_tokens = img_features.flatten(2).transpose(1, 2)  # [B, H*W, C]
        wm_tokens = wm_features.flatten(2).transpose(1, 2)    # [B, S*S, C]

        # 4. Cross-attention layers
        for attn_layer in self.cross_attention_layers:
            img_tokens = attn_layer(img_tokens, wm_tokens)

        # 5. Reshape back to spatial
        fused = img_tokens.transpose(1, 2).view(B, C, H, W)

        # 6. Decode to residual
        residual = self.decoder(fused)

        # 7. Ensure residual matches image size
        if residual.shape[-2:] != image.shape[-2:]:
            residual = F.interpolate(
                residual, size=image.shape[-2:], mode="bilinear", align_corners=False
            )

        # 8. Scale and apply residual
        residual = torch.tanh(residual) * self.residual_scale
        watermarked = image + residual

        return torch.clamp(watermarked, 0.0, 1.0)

    def get_residual(
        self, image: torch.Tensor, watermark: torch.Tensor
    ) -> torch.Tensor:
        """Get the raw residual without adding to image (for visualization).

        Args:
            image: Input images [B, 3, H, W].
            watermark: Binary payload [B, payload_bits].

        Returns:
            Residual tensor [B, 3, H, W].
        """
        watermarked = self.forward(image, watermark)
        return watermarked - image
