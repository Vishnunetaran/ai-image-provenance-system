"""
Watermark Attacker Network (U-Net).

Implements a U-Net model trained to remove watermarks from images while
preserving visual quality.  Used during adversarial training to make the
encoder-decoder pair robust against removal attacks.

Architecture:
    Standard U-Net with:
    - 4 encoder blocks (downsample by 2× each)
    - 4 decoder blocks (upsample by 2× each)
    - Skip connections between encoder and decoder
    - Input: watermarked image [B, 3, H, W]
    - Output: "cleaned" image [B, 3, H, W]

References:
    - U-Net (Ronneberger et al., 2015)
    - Adversarial Watermarking Transformer (2024)
"""

import logging

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError:
    raise ImportError("PyTorch is required: pip install torch")


class ConvBlock(nn.Module):
    """Double convolution block with BatchNorm and GELU activation."""

    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.GELU(),
            nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.GELU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class DownBlock(nn.Module):
    """Encoder block: Conv → MaxPool (downsample 2×)."""

    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.conv = ConvBlock(in_ch, out_ch)
        self.pool = nn.MaxPool2d(2)

    def forward(self, x: torch.Tensor):
        skip = self.conv(x)
        down = self.pool(skip)
        return down, skip


class UpBlock(nn.Module):
    """Decoder block: Upsample → Concatenate skip → Conv."""

    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.up = nn.ConvTranspose2d(in_ch, out_ch, 2, stride=2)
        self.conv = ConvBlock(out_ch * 2, out_ch)  # *2 for skip concat

    def forward(self, x: torch.Tensor, skip: torch.Tensor) -> torch.Tensor:
        x = self.up(x)
        # Handle size mismatches
        if x.shape != skip.shape:
            x = F.interpolate(x, size=skip.shape[2:], mode="bilinear", align_corners=False)
        x = torch.cat([x, skip], dim=1)
        return self.conv(x)


class WatermarkAttacker(nn.Module):
    """U-Net watermark removal network.

    Attempts to remove embedded watermarks while preserving image quality.
    Used as the adversary in min-max adversarial training.

    Args:
        in_channels: Number of input channels (default 3 for RGB).
        base_channels: Base number of feature channels (default 64).

    Example:
        >>> attacker = WatermarkAttacker()
        >>> watermarked_image = torch.randn(2, 3, 256, 256)
        >>> cleaned = attacker(watermarked_image)
        >>> cleaned.shape
        torch.Size([2, 3, 256, 256])
    """

    def __init__(self, in_channels: int = 3, base_channels: int = 64):
        super().__init__()

        ch = base_channels

        # Encoder path
        self.enc1 = DownBlock(in_channels, ch)       # 256 → 128
        self.enc2 = DownBlock(ch, ch * 2)             # 128 → 64
        self.enc3 = DownBlock(ch * 2, ch * 4)         # 64 → 32
        self.enc4 = DownBlock(ch * 4, ch * 8)         # 32 → 16

        # Bottleneck
        self.bottleneck = ConvBlock(ch * 8, ch * 16)

        # Decoder path
        self.dec4 = UpBlock(ch * 16, ch * 8)          # 16 → 32
        self.dec3 = UpBlock(ch * 8, ch * 4)           # 32 → 64
        self.dec2 = UpBlock(ch * 4, ch * 2)           # 64 → 128
        self.dec1 = UpBlock(ch * 2, ch)               # 128 → 256

        # Output
        self.output_conv = nn.Sequential(
            nn.Conv2d(ch, ch // 2, 3, padding=1),
            nn.GELU(),
            nn.Conv2d(ch // 2, in_channels, 1),
        )

        self._init_weights()

    def _init_weights(self):
        """Initialize for near-identity output (residual learning)."""
        # Zero-init the final conv → start as identity function
        nn.init.zeros_(self.output_conv[-1].weight)
        if self.output_conv[-1].bias is not None:
            nn.init.zeros_(self.output_conv[-1].bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Remove watermark from image.

        Args:
            x: Watermarked images [B, 3, H, W] in [0, 1].

        Returns:
            Cleaned images [B, 3, H, W] in [0, 1].
        """
        # Encoder
        d1, skip1 = self.enc1(x)
        d2, skip2 = self.enc2(d1)
        d3, skip3 = self.enc3(d2)
        d4, skip4 = self.enc4(d3)

        # Bottleneck
        bn = self.bottleneck(d4)

        # Decoder
        u4 = self.dec4(bn, skip4)
        u3 = self.dec3(u4, skip3)
        u2 = self.dec2(u3, skip2)
        u1 = self.dec1(u2, skip1)

        # Residual output
        residual = self.output_conv(u1)

        # Ensure output size matches input
        if residual.shape[2:] != x.shape[2:]:
            residual = F.interpolate(
                residual, size=x.shape[2:], mode="bilinear", align_corners=False
            )

        # Apply residual (start near identity)
        cleaned = torch.clamp(x + residual, 0, 1)

        return cleaned

    def get_removal_residual(self, x: torch.Tensor) -> torch.Tensor:
        """Get the residual that the attacker wants to apply.

        Useful for visualizing what the attacker is doing.

        Args:
            x: Watermarked images.

        Returns:
            Residual tensor.
        """
        return self.forward(x) - x
