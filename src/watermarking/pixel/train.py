"""
Joint Encoder-Decoder Training Pipeline.

Trains the watermark encoder and decoder end-to-end with a combined loss:
    L = λ_acc × BCE(extracted_bits, watermark) + λ_qual × (LPIPS + MSE)

Supports:
    - Clean-image pre-training (mild augmentations)
    - Progressive augmentation (curriculum learning)
    - Gradient norm monitoring for debugging
    - PSNR / SSIM / bit-accuracy logging

Usage:
    python -m src.watermarking.pixel.train --config configs/encoder_decoder_config.yaml
"""

import os
import time
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import Dataset, DataLoader
    import torchvision.transforms as T
except ImportError:
    raise ImportError("PyTorch + torchvision required: pip install torch torchvision")

try:
    import yaml
except ImportError:
    yaml = None

from src.watermarking.pixel.encoder import WatermarkEncoder
from src.watermarking.pixel.decoder import WatermarkDecoder


# ──────────────────────────────────────────────────────────────────────
# Differentiable Augmentations
# ──────────────────────────────────────────────────────────────────────


class DifferentiableJPEG(nn.Module):
    """Approximate differentiable JPEG compression.

    Uses a simple noise + quantisation approximation for training.
    Not an exact JPEG simulation, but sufficient for gradient flow.
    """

    def __init__(self, quality_range: Tuple[int, int] = (50, 95)):
        super().__init__()
        self.q_min, self.q_max = quality_range

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if not self.training:
            return x
        # Simulate JPEG artefacts with additive noise scaled by quality
        quality = torch.randint(self.q_min, self.q_max + 1, (1,)).item()
        noise_level = (100 - quality) / 100.0 * 0.05
        noise = torch.randn_like(x) * noise_level
        return torch.clamp(x + noise, 0, 1)


class AugmentationPipeline(nn.Module):
    """Differentiable augmentation pipeline for training robustness.

    Applies random combinations of:
        - Approximate JPEG compression
        - Random resize (with bilinear interpolation)
        - Gaussian blur
        - Color jitter (brightness, contrast)
    """

    def __init__(self, strength: float = 0.5):
        """
        Args:
            strength: Augmentation intensity in [0, 1].
                0.0 = no augmentations, 1.0 = full strength.
        """
        super().__init__()
        self.strength = strength
        self.jpeg = DifferentiableJPEG(quality_range=(30, 95))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if not self.training or self.strength < 0.01:
            return x

        # Random JPEG (50% probability)
        if torch.rand(1).item() < 0.5 * self.strength:
            x = self.jpeg(x)

        # Random Gaussian blur (30% probability)
        if torch.rand(1).item() < 0.3 * self.strength:
            sigma = torch.rand(1).item() * 1.5 * self.strength + 0.1
            kernel_size = int(sigma * 6) | 1
            kernel_size = max(kernel_size, 3)
            if kernel_size % 2 == 0:
                kernel_size += 1
            x = T.functional.gaussian_blur(x, kernel_size, sigma)

        # Random brightness (40% probability)
        if torch.rand(1).item() < 0.4 * self.strength:
            factor = 1.0 + (torch.rand(1).item() - 0.5) * 0.4 * self.strength
            x = torch.clamp(x * factor, 0, 1)

        return x


# ──────────────────────────────────────────────────────────────────────
# Synthetic Dataset
# ──────────────────────────────────────────────────────────────────────


class SyntheticWatermarkDataset(Dataset):
    """Synthetic dataset for development / testing.

    Generates random images and random watermark payloads on the fly.
    For production, replace with a real dataset of AI-generated images.
    """

    def __init__(self, num_samples: int = 1000, image_size: int = 256, payload_bits: int = 160):
        self.num_samples = num_samples
        self.image_size = image_size
        self.payload_bits = payload_bits

    def __len__(self):
        return self.num_samples

    def __getitem__(self, idx):
        # Random structured image (gradient + noise)
        img = torch.rand(3, self.image_size, self.image_size) * 0.8 + 0.1
        # Add some structure
        x = torch.linspace(0, 1, self.image_size).unsqueeze(0).expand(self.image_size, -1)
        img[0] += x * 0.15
        img[1] += x.T * 0.15
        img = torch.clamp(img, 0, 1)

        watermark = torch.randint(0, 2, (self.payload_bits,)).float()
        return img, watermark


# ──────────────────────────────────────────────────────────────────────
# Loss Functions
# ──────────────────────────────────────────────────────────────────────


def compute_psnr(original: torch.Tensor, modified: torch.Tensor) -> float:
    """Compute Peak Signal-to-Noise Ratio (in dB)."""
    mse = F.mse_loss(original, modified).item()
    if mse < 1e-10:
        return 100.0
    return 10.0 * np.log10(1.0 / mse)


def compute_bit_accuracy(predicted: torch.Tensor, target: torch.Tensor) -> float:
    """Compute fraction of correctly predicted bits."""
    pred_bits = (torch.sigmoid(predicted) > 0.5).float()
    return (pred_bits == target).float().mean().item()


# ──────────────────────────────────────────────────────────────────────
# Trainer
# ──────────────────────────────────────────────────────────────────────


class WatermarkTrainer:
    """Joint encoder-decoder trainer.

    Handles:
        - Training loop with combined accuracy + quality loss
        - Curriculum-based augmentation scheduling
        - Gradient norm monitoring
        - Checkpoint saving and loading

    Args:
        encoder: WatermarkEncoder instance.
        decoder: WatermarkDecoder instance.
        config: Training configuration dictionary.
    """

    def __init__(
        self,
        encoder: WatermarkEncoder,
        decoder: WatermarkDecoder,
        config: Optional[Dict] = None,
    ):
        self.encoder = encoder
        self.decoder = decoder

        # Default config
        cfg = {
            "batch_size": 32,
            "learning_rate": 1e-4,
            "epochs": 50,
            "lambda_accuracy": 0.7,
            "lambda_quality": 0.3,
            "image_size": 256,
            "payload_bits": 160,
            "checkpoint_dir": "checkpoints/pixel_watermark",
            "log_interval": 10,
            "augmentation_warmup_epochs": 5,
        }
        if config:
            cfg.update(config)
        self.config = cfg

        # Device
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.encoder = self.encoder.to(self.device)
        self.decoder = self.decoder.to(self.device)

        # Optimizer
        params = list(self.encoder.parameters()) + list(self.decoder.parameters())
        self.optimizer = torch.optim.AdamW(
            params, lr=cfg["learning_rate"], weight_decay=1e-5
        )

        # LR scheduler
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, T_max=cfg["epochs"], eta_min=1e-6
        )

        # Augmentation pipeline
        self.augmentation = AugmentationPipeline(strength=0.0)

        # Loss
        self.bce_loss = nn.BCEWithLogitsLoss()

        # LPIPS (optional)
        self.lpips_fn = None
        try:
            import lpips
            self.lpips_fn = lpips.LPIPS(net="alex").to(self.device)
            self.lpips_fn.eval()
            for p in self.lpips_fn.parameters():
                p.requires_grad = False
        except ImportError:
            logger.warning("LPIPS not available; using MSE-only quality loss")

    def _quality_loss(self, original: torch.Tensor, watermarked: torch.Tensor) -> torch.Tensor:
        """Compute perceptual quality loss.

        Combines MSE and LPIPS (if available) for balanced quality assessment.
        """
        mse = F.mse_loss(watermarked, original)

        if self.lpips_fn is not None:
            # LPIPS expects [-1, 1] range
            lpips_val = self.lpips_fn(original * 2 - 1, watermarked * 2 - 1).mean()
            return mse + 0.5 * lpips_val
        else:
            return mse

    def _update_augmentation_strength(self, epoch: int):
        """Curriculum learning: gradually increase augmentation strength."""
        warmup = self.config["augmentation_warmup_epochs"]
        if epoch < warmup:
            strength = 0.0
        else:
            progress = (epoch - warmup) / max(self.config["epochs"] - warmup, 1)
            strength = min(progress, 1.0) * 0.8  # Max 80% strength
        self.augmentation.strength = strength

    def _compute_gradient_norm(self) -> float:
        """Compute total gradient norm for monitoring."""
        total_norm = 0.0
        for model in [self.encoder, self.decoder]:
            for p in model.parameters():
                if p.grad is not None:
                    total_norm += p.grad.data.norm(2).item() ** 2
        return total_norm ** 0.5

    def train_epoch(
        self, dataloader: DataLoader, epoch: int
    ) -> Dict[str, float]:
        """Train for one epoch.

        Args:
            dataloader: Training data loader.
            epoch: Current epoch number.

        Returns:
            Dictionary of average metrics for the epoch.
        """
        self.encoder.train()
        self.decoder.train()
        self._update_augmentation_strength(epoch)

        total_loss = 0.0
        total_acc_loss = 0.0
        total_qual_loss = 0.0
        total_bit_acc = 0.0
        total_psnr = 0.0
        num_batches = 0

        for batch_idx, (images, watermarks) in enumerate(dataloader):
            images = images.to(self.device)
            watermarks = watermarks.to(self.device)

            # Forward: encode watermark
            watermarked = self.encoder(images, watermarks)

            # Apply augmentation (if training)
            augmented = self.augmentation(watermarked)

            # Forward: decode watermark
            logits = self.decoder(augmented)

            # Losses
            acc_loss = self.bce_loss(logits, watermarks)
            qual_loss = self._quality_loss(images, watermarked)

            λ_acc = self.config["lambda_accuracy"]
            λ_qual = self.config["lambda_quality"]
            loss = λ_acc * acc_loss + λ_qual * qual_loss

            # Backward
            self.optimizer.zero_grad()
            loss.backward()

            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(
                list(self.encoder.parameters()) + list(self.decoder.parameters()),
                max_norm=1.0,
            )

            self.optimizer.step()

            # Metrics
            with torch.no_grad():
                bit_acc = compute_bit_accuracy(logits, watermarks)
                psnr = compute_psnr(images, watermarked)

            total_loss += loss.item()
            total_acc_loss += acc_loss.item()
            total_qual_loss += qual_loss.item()
            total_bit_acc += bit_acc
            total_psnr += psnr
            num_batches += 1

            if (batch_idx + 1) % self.config["log_interval"] == 0:
                grad_norm = self._compute_gradient_norm()
                logger.info(
                    f"  Batch {batch_idx+1}/{len(dataloader)}: "
                    f"loss={loss.item():.4f}, acc={bit_acc:.2%}, "
                    f"psnr={psnr:.1f}dB, |∇|={grad_norm:.4f}"
                )

        self.scheduler.step()

        return {
            "loss": total_loss / max(num_batches, 1),
            "accuracy_loss": total_acc_loss / max(num_batches, 1),
            "quality_loss": total_qual_loss / max(num_batches, 1),
            "bit_accuracy": total_bit_acc / max(num_batches, 1),
            "psnr": total_psnr / max(num_batches, 1),
            "augmentation_strength": self.augmentation.strength,
            "learning_rate": self.scheduler.get_last_lr()[0],
        }

    @torch.no_grad()
    def validate(self, dataloader: DataLoader) -> Dict[str, float]:
        """Run validation.

        Args:
            dataloader: Validation data loader.

        Returns:
            Dictionary of average validation metrics.
        """
        self.encoder.eval()
        self.decoder.eval()

        total_bit_acc = 0.0
        total_psnr = 0.0
        num_batches = 0

        for images, watermarks in dataloader:
            images = images.to(self.device)
            watermarks = watermarks.to(self.device)

            watermarked = self.encoder(images, watermarks)
            logits = self.decoder(watermarked)

            total_bit_acc += compute_bit_accuracy(logits, watermarks)
            total_psnr += compute_psnr(images, watermarked)
            num_batches += 1

        return {
            "val_bit_accuracy": total_bit_acc / max(num_batches, 1),
            "val_psnr": total_psnr / max(num_batches, 1),
        }

    def train(
        self,
        train_loader: DataLoader,
        val_loader: Optional[DataLoader] = None,
    ) -> list:
        """Full training loop.

        Args:
            train_loader: Training DataLoader.
            val_loader: Optional validation DataLoader.

        Returns:
            List of per-epoch metric dictionaries.
        """
        history = []
        best_acc = 0.0

        logger.info(
            f"Starting training: {self.config['epochs']} epochs, "
            f"lr={self.config['learning_rate']}, device={self.device}"
        )

        for epoch in range(self.config["epochs"]):
            t0 = time.time()

            # Train
            train_metrics = self.train_epoch(train_loader, epoch)

            # Validate
            val_metrics = {}
            if val_loader is not None:
                val_metrics = self.validate(val_loader)

            elapsed = time.time() - t0
            metrics = {**train_metrics, **val_metrics, "epoch": epoch, "time_s": elapsed}
            history.append(metrics)

            # Logging
            logger.info(f"Epoch {epoch+1}/{self.config['epochs']} ({elapsed:.1f}s)")
            logger.info(f"  Accuracy: {train_metrics['bit_accuracy']:.2%}")
            logger.info(f"  PSNR:     {train_metrics['psnr']:.2f}dB")
            logger.info(f"  Loss:     {train_metrics['loss']:.4f}")
            if val_metrics:
                logger.info(f"  Val Acc:  {val_metrics['val_bit_accuracy']:.2%}")
                logger.info(f"  Val PSNR: {val_metrics['val_psnr']:.2f}dB")

            # Save best checkpoint
            acc = val_metrics.get("val_bit_accuracy", train_metrics["bit_accuracy"])
            if acc > best_acc:
                best_acc = acc
                self.save_checkpoint(epoch, "best")

            # Periodic checkpoint
            if (epoch + 1) % 10 == 0:
                self.save_checkpoint(epoch, f"epoch_{epoch+1}")

        logger.info(f"Training complete. Best accuracy: {best_acc:.2%}")
        return history

    def save_checkpoint(self, epoch: int, tag: str = "latest"):
        """Save model checkpoint.

        Args:
            epoch: Current epoch number.
            tag: Checkpoint tag (e.g. "best", "epoch_50").
        """
        ckpt_dir = Path(self.config["checkpoint_dir"])
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        path = ckpt_dir / f"checkpoint_{tag}.pt"
        torch.save(
            {
                "epoch": epoch,
                "encoder_state": self.encoder.state_dict(),
                "decoder_state": self.decoder.state_dict(),
                "optimizer_state": self.optimizer.state_dict(),
                "scheduler_state": self.scheduler.state_dict(),
                "config": self.config,
            },
            path,
        )
        logger.info(f"Checkpoint saved: {path}")

    def load_checkpoint(self, path: str):
        """Load model checkpoint.

        Args:
            path: Path to checkpoint file.
        """
        ckpt = torch.load(path, map_location=self.device)
        self.encoder.load_state_dict(ckpt["encoder_state"])
        self.decoder.load_state_dict(ckpt["decoder_state"])
        self.optimizer.load_state_dict(ckpt["optimizer_state"])
        self.scheduler.load_state_dict(ckpt["scheduler_state"])
        logger.info(f"Checkpoint loaded: {path} (epoch {ckpt['epoch']})")


# ──────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────────────────────────────


def main():
    """Run training from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Train pixel watermark encoder-decoder")
    parser.add_argument("--config", type=str, default="configs/encoder_decoder_config.yaml")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    # Load config
    config = {}
    if yaml and os.path.exists(args.config):
        with open(args.config) as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {args.config}")

    # CLI overrides
    if args.epochs:
        config["epochs"] = args.epochs
    if args.batch_size:
        config["batch_size"] = args.batch_size
    if args.lr:
        config["learning_rate"] = args.lr

    payload_bits = config.get("payload_bits", 160)
    image_size = config.get("image_size", 256)

    # Models
    encoder = WatermarkEncoder(payload_bits=payload_bits, image_size=image_size)
    decoder = WatermarkDecoder(payload_bits=payload_bits, image_size=image_size)

    # Data (synthetic for now)
    train_ds = SyntheticWatermarkDataset(num_samples=1000, image_size=image_size, payload_bits=payload_bits)
    val_ds = SyntheticWatermarkDataset(num_samples=200, image_size=image_size, payload_bits=payload_bits)
    train_loader = DataLoader(train_ds, batch_size=config.get("batch_size", 32), shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=config.get("batch_size", 32), shuffle=False, num_workers=0)

    # Train
    trainer = WatermarkTrainer(encoder, decoder, config)
    history = trainer.train(train_loader, val_loader)

    logger.info("Done!")


if __name__ == "__main__":
    main()
