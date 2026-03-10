"""
CNN + ViT Hybrid Watermark Decoder.

Extracts the embedded binary watermark payload from a (potentially
transformed) image.  Mirrors the encoder architecture with an
EfficientNet-B4 backbone for local features and a small ViT for global
pattern recognition.

Architecture:
    Input: image [B, 3, H, W]
      → EfficientNet-B4 feature extractor
      → Feature projection to token sequence
      → 4-layer ViT with self-attention
      → Global average pooling
      → MLP classification head → [B, payload_bits] logits

References:
    - Stable Signature (Fernandez et al., Meta 2023)
    - EfficientNet (Tan & Le, 2019)
"""

import logging
from typing import Optional

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


# ──────────────────────────────────────────────────────────────────────
# Transformer Block
# ──────────────────────────────────────────────────────────────────────


class TransformerBlock(nn.Module):
    """Standard transformer encoder block with pre-norm."""

    def __init__(self, dim: int, num_heads: int = 4, mlp_ratio: float = 4.0, dropout: float = 0.1):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim)
        self.attn = nn.MultiheadAttention(
            embed_dim=dim, num_heads=num_heads, dropout=dropout, batch_first=True
        )
        self.norm2 = nn.LayerNorm(dim)
        self.mlp = nn.Sequential(
            nn.Linear(dim, int(dim * mlp_ratio)),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(int(dim * mlp_ratio), dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Self-attention with residual
        normed = self.norm1(x)
        attended, _ = self.attn(normed, normed, normed)
        x = x + attended
        # FFN with residual
        x = x + self.mlp(self.norm2(x))
        return x


# ──────────────────────────────────────────────────────────────────────
# Main Decoder
# ──────────────────────────────────────────────────────────────────────


class WatermarkDecoder(nn.Module):
    """CNN + ViT Hybrid Watermark Decoder.

    Extracts binary watermark bits from an image.  Uses EfficientNet-B4
    for spatial feature extraction and a small ViT for global pattern
    analysis.  Outputs per-bit logits for the watermark payload.

    Args:
        payload_bits: Number of watermark bits to extract (default 160).
        image_size: Expected input image size (default 256).
        feature_dim: Internal ViT dimension (default 256).
        num_transformer_layers: Number of ViT layers (default 4).
        use_pretrained_backbone: Use pretrained EfficientNet weights.

    Example:
        >>> decoder = WatermarkDecoder(payload_bits=160, image_size=256)
        >>> image = torch.randn(2, 3, 256, 256)
        >>> logits = decoder(image)
        >>> logits.shape
        torch.Size([2, 160])
        >>> bits = (torch.sigmoid(logits) > 0.5).int()
    """

    def __init__(
        self,
        payload_bits: int = 160,
        image_size: int = 256,
        feature_dim: int = 256,
        num_transformer_layers: int = 4,
        use_pretrained_backbone: bool = True,
    ):
        super().__init__()

        self.payload_bits = payload_bits
        self.feature_dim = feature_dim

        # ── CNN Backbone ──────────────────────────────────────────
        if timm is not None and use_pretrained_backbone:
            self.backbone = timm.create_model(
                "efficientnet_b4",
                pretrained=True,
                features_only=True,
                out_indices=(3,),  # Use stage 3 features
            )
            # EfficientNet-B4 stage 3 has 160 channels
            backbone_out_channels = 160
        else:
            self.backbone = nn.Sequential(
                nn.Conv2d(3, 64, 7, stride=2, padding=3),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.MaxPool2d(3, stride=2, padding=1),
                nn.Conv2d(64, 128, 3, stride=2, padding=1),
                nn.BatchNorm2d(128),
                nn.ReLU(),
                nn.Conv2d(128, 256, 3, stride=2, padding=1),
                nn.BatchNorm2d(256),
                nn.ReLU(),
            )
            backbone_out_channels = 256

        # ── Feature Projection ────────────────────────────────────
        self.feature_proj = nn.Sequential(
            nn.Conv2d(backbone_out_channels, feature_dim, 1),
            nn.BatchNorm2d(feature_dim),
            nn.GELU(),
        )

        # ── Positional Embedding ──────────────────────────────────
        # Computed dynamically based on feature map size
        self.pos_embed = None  # Lazy init

        # ── Transformer Layers ────────────────────────────────────
        self.transformer = nn.ModuleList(
            [
                TransformerBlock(feature_dim, num_heads=4)
                for _ in range(num_transformer_layers)
            ]
        )

        # ── Classification Head ───────────────────────────────────
        self.norm = nn.LayerNorm(feature_dim)
        self.head = nn.Sequential(
            nn.Linear(feature_dim, feature_dim * 2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(feature_dim * 2, feature_dim),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(feature_dim, payload_bits),
        )

    def _get_pos_embed(self, num_tokens: int, device: torch.device) -> torch.Tensor:
        """Get or create positional embeddings.

        Uses sinusoidal positional encoding (no learned parameters needed).
        """
        dim = self.feature_dim
        position = torch.arange(num_tokens, device=device).unsqueeze(1).float()
        div_term = torch.exp(
            torch.arange(0, dim, 2, device=device).float() * -(
                torch.log(torch.tensor(10000.0)) / dim
            )
        )

        pe = torch.zeros(1, num_tokens, dim, device=device)
        pe[0, :, 0::2] = torch.sin(position * div_term)
        pe[0, :, 1::2] = torch.cos(position * div_term[: dim // 2])
        return pe

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        """Extract watermark from image.

        Args:
            image: Input images [B, 3, H, W] normalised to [0, 1].

        Returns:
            Logits for each watermark bit [B, payload_bits].
            Apply ``torch.sigmoid()`` for probabilities.
        """
        # 1. Extract CNN features
        if timm is not None and hasattr(self.backbone, "feature_info"):
            features = self.backbone(image)
            x = features[-1]
        else:
            x = self.backbone(image)

        # 2. Project to feature_dim
        x = self.feature_proj(x)  # [B, D, H', W']

        # 3. Reshape to token sequence
        B, D, H, W = x.shape
        tokens = x.flatten(2).transpose(1, 2)  # [B, H'*W', D]

        # 4. Add positional encoding
        pos = self._get_pos_embed(tokens.shape[1], tokens.device)
        tokens = tokens + pos

        # 5. Transformer layers
        for layer in self.transformer:
            tokens = layer(tokens)

        # 6. Global average pooling
        tokens = self.norm(tokens)
        pooled = tokens.mean(dim=1)  # [B, D]

        # 7. Classification head
        logits = self.head(pooled)  # [B, payload_bits]

        return logits

    def extract_bits(self, image: torch.Tensor) -> torch.Tensor:
        """Extract hard-decision watermark bits.

        Args:
            image: Input images [B, 3, H, W].

        Returns:
            Binary tensor [B, payload_bits] with values in {0, 1}.
        """
        logits = self.forward(image)
        return (torch.sigmoid(logits) > 0.5).int()

    def extract_soft(self, image: torch.Tensor) -> torch.Tensor:
        """Extract soft-decision watermark probabilities.

        Args:
            image: Input images [B, 3, H, W].

        Returns:
            Probability tensor [B, payload_bits] in [0, 1].
        """
        logits = self.forward(image)
        return torch.sigmoid(logits)
