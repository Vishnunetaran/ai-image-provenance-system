"""
Adversarial Training for Watermark Robustness.

Implements a min-max training loop where:
    - Encoder + Decoder are trained to embed/extract watermarks robustly
    - Attacker (U-Net) is trained to remove watermarks
    - All three networks are updated alternately

Training Loop:
    for each batch:
        1. Encoder embeds watermark → watermarked image
        2. Attacker tries to remove watermark → attacked image
        3. Decoder extracts watermark from attacked image
        4. Encoder/Decoder loss = accuracy(extracted, target) + quality(attacked, original)
        5. Attacker loss = -accuracy(extracted, target) + similarity(attacked, watermarked)
        6. Update encoder+decoder (minimize their loss)
        7. Update attacker (minimize its loss = maximize removal)

This creates an arms race that produces extremely robust watermarks.

References:
    - Adversarial Watermarking Transformer (2024)
    - GAN training principles (Goodfellow et al., 2014)
"""

import os
import time
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader
except ImportError:
    raise ImportError("PyTorch required: pip install torch")

from src.watermarking.pixel.encoder import WatermarkEncoder
from src.watermarking.pixel.decoder import WatermarkDecoder
from src.watermarking.pixel.attacker import WatermarkAttacker
from src.watermarking.pixel.train import (
    AugmentationPipeline,
    SyntheticWatermarkDataset,
    compute_psnr,
    compute_bit_accuracy,
)


class AdversarialTrainer:
    """Adversarial training for robust watermarking.

    Orchestrates the min-max game between encoder/decoder and attacker.

    Args:
        encoder: WatermarkEncoder model.
        decoder: WatermarkDecoder model.
        attacker: WatermarkAttacker model.
        config: Training configuration dictionary.

    Example:
        >>> encoder = WatermarkEncoder(payload_bits=160)
        >>> decoder = WatermarkDecoder(payload_bits=160)
        >>> attacker = WatermarkAttacker()
        >>> trainer = AdversarialTrainer(encoder, decoder, attacker)
        >>> trainer.train(train_loader)
    """

    def __init__(
        self,
        encoder: WatermarkEncoder,
        decoder: WatermarkDecoder,
        attacker: WatermarkAttacker,
        config: Optional[Dict] = None,
    ):
        cfg = {
            "batch_size": 16,
            "lr_encoder_decoder": 5e-5,
            "lr_attacker": 1e-4,
            "epochs": 100,
            "lambda_accuracy": 0.8,
            "lambda_quality": 0.2,
            "lambda_attacker_quality": 0.5,
            "attacker_warmup_epochs": 5,
            "encoder_decoder_steps": 1,
            "attacker_steps": 1,
            "augmentation_strength": 0.8,
            "checkpoint_dir": "checkpoints/adversarial",
            "log_interval": 5,
        }
        if config:
            cfg.update(config)
        self.config = cfg

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.encoder = encoder.to(self.device)
        self.decoder = decoder.to(self.device)
        self.attacker = attacker.to(self.device)

        # Separate optimizers
        self.opt_ed = torch.optim.AdamW(
            list(encoder.parameters()) + list(decoder.parameters()),
            lr=cfg["lr_encoder_decoder"],
            weight_decay=1e-5,
        )
        self.opt_atk = torch.optim.AdamW(
            attacker.parameters(),
            lr=cfg["lr_attacker"],
            weight_decay=1e-5,
        )

        # Schedulers
        self.sch_ed = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.opt_ed, T_max=cfg["epochs"], eta_min=1e-6
        )
        self.sch_atk = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.opt_atk, T_max=cfg["epochs"], eta_min=1e-6
        )

        # Heavy augmentation
        self.augmentation = AugmentationPipeline(strength=cfg["augmentation_strength"])

        self.bce_loss = nn.BCEWithLogitsLoss()

    def _encoder_decoder_step(
        self, images: torch.Tensor, watermarks: torch.Tensor
    ) -> Dict[str, float]:
        """One optimization step for the encoder and decoder.

        Goal: Embed watermarks that survive both the attacker AND augmentation.
        """
        self.encoder.train()
        self.decoder.train()
        self.attacker.eval()  # Don't update attacker gradients

        # 1. Embed watermark
        watermarked = self.encoder(images, watermarks)

        # 2. Attacker tries to remove (no grad for attacker)
        with torch.no_grad():
            attacked = self.attacker(watermarked)

        # 3. Apply augmentation on top of attack
        self.augmentation.train()
        augmented = self.augmentation(attacked)

        # 4. Decoder extracts from attacked+augmented image
        logits = self.decoder(augmented)

        # 5. Losses
        acc_loss = self.bce_loss(logits, watermarks)
        qual_loss = F.mse_loss(watermarked, images)

        λ_a = self.config["lambda_accuracy"]
        λ_q = self.config["lambda_quality"]
        total_loss = λ_a * acc_loss + λ_q * qual_loss

        # 6. Update
        self.opt_ed.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(self.encoder.parameters()) + list(self.decoder.parameters()),
            max_norm=1.0,
        )
        self.opt_ed.step()

        with torch.no_grad():
            bit_acc = compute_bit_accuracy(logits, watermarks)
            psnr = compute_psnr(images, watermarked)

        return {
            "ed_loss": total_loss.item(),
            "ed_acc_loss": acc_loss.item(),
            "ed_qual_loss": qual_loss.item(),
            "ed_bit_accuracy": bit_acc,
            "ed_psnr": psnr,
        }

    def _attacker_step(
        self, images: torch.Tensor, watermarks: torch.Tensor
    ) -> Dict[str, float]:
        """One optimization step for the attacker.

        Goal: Remove watermarks while keeping the image looking similar.
        """
        self.encoder.eval()
        self.decoder.eval()
        self.attacker.train()

        # 1. Embed watermark (no grad for encoder)
        with torch.no_grad():
            watermarked = self.encoder(images, watermarks)

        # 2. Attacker removes watermark
        attacked = self.attacker(watermarked)

        # 3. Decoder tries to extract (no grad for decoder)
        with torch.no_grad():
            logits = self.decoder(attacked)

        # 4. Attacker wants:
        #    - LOW accuracy (maximize removal) → minimize negative BCE
        #    - HIGH similarity to watermarked image (preserve quality)
        acc_penalty = -self.bce_loss(logits, watermarks)  # Negative = maximize error
        quality_incentive = F.mse_loss(attacked, watermarked.detach())

        λ_q = self.config["lambda_attacker_quality"]
        total_loss = acc_penalty + λ_q * quality_incentive

        # 5. Update
        self.opt_atk.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.attacker.parameters(), max_norm=1.0)
        self.opt_atk.step()

        with torch.no_grad():
            bit_acc = compute_bit_accuracy(logits, watermarks)
            atk_psnr = compute_psnr(watermarked, attacked)

        return {
            "atk_loss": total_loss.item(),
            "atk_removal_rate": 1.0 - bit_acc,
            "atk_psnr": atk_psnr,
        }

    def train_epoch(
        self, dataloader: DataLoader, epoch: int
    ) -> Dict[str, float]:
        """Run one epoch of adversarial training.

        Args:
            dataloader: Training data loader.
            epoch: Current epoch number.

        Returns:
            Average metrics for the epoch.
        """
        metrics_sum: Dict[str, float] = {}
        num_batches = 0

        warmup = self.config["attacker_warmup_epochs"]
        train_attacker = epoch >= warmup

        for batch_idx, (images, watermarks) in enumerate(dataloader):
            images = images.to(self.device)
            watermarks = watermarks.to(self.device)

            # Encoder/Decoder step(s)
            for _ in range(self.config["encoder_decoder_steps"]):
                ed_metrics = self._encoder_decoder_step(images, watermarks)

            # Attacker step(s) (after warmup)
            atk_metrics = {}
            if train_attacker:
                for _ in range(self.config["attacker_steps"]):
                    atk_metrics = self._attacker_step(images, watermarks)

            # Accumulate
            combined = {**ed_metrics, **atk_metrics}
            for k, v in combined.items():
                metrics_sum[k] = metrics_sum.get(k, 0) + v
            num_batches += 1

            if (batch_idx + 1) % self.config["log_interval"] == 0:
                acc = ed_metrics["ed_bit_accuracy"]
                rem = atk_metrics.get("atk_removal_rate", 0)
                logger.info(
                    f"  Batch {batch_idx+1}: "
                    f"ED_acc={acc:.2%}, ATK_removal={rem:.2%}, "
                    f"ED_psnr={ed_metrics['ed_psnr']:.1f}dB"
                )

        self.sch_ed.step()
        if train_attacker:
            self.sch_atk.step()

        return {k: v / max(num_batches, 1) for k, v in metrics_sum.items()}

    def train(
        self,
        train_loader: DataLoader,
        val_loader: Optional[DataLoader] = None,
    ) -> list:
        """Full adversarial training loop.

        Args:
            train_loader: Training DataLoader.
            val_loader: Optional validation DataLoader.

        Returns:
            List of per-epoch metrics.
        """
        history = []
        best_acc = 0.0

        logger.info(
            f"Adversarial training: {self.config['epochs']} epochs, "
            f"attacker warmup={self.config['attacker_warmup_epochs']} epochs, "
            f"device={self.device}"
        )

        for epoch in range(self.config["epochs"]):
            t0 = time.time()
            metrics = self.train_epoch(train_loader, epoch)
            elapsed = time.time() - t0

            metrics["epoch"] = epoch
            metrics["time_s"] = elapsed
            history.append(metrics)

            logger.info(f"Epoch {epoch+1}/{self.config['epochs']} ({elapsed:.1f}s)")
            logger.info(f"  ED Accuracy:    {metrics.get('ed_bit_accuracy', 0):.2%}")
            logger.info(f"  ED PSNR:        {metrics.get('ed_psnr', 0):.2f}dB")
            if "atk_removal_rate" in metrics:
                logger.info(f"  ATK Removal:    {metrics['atk_removal_rate']:.2%}")

            # Save best
            acc = metrics.get("ed_bit_accuracy", 0)
            if acc > best_acc:
                best_acc = acc
                self._save_checkpoint(epoch, "best")

            if (epoch + 1) % 10 == 0:
                self._save_checkpoint(epoch, f"epoch_{epoch+1}")

        return history

    def _save_checkpoint(self, epoch: int, tag: str):
        """Save all three model checkpoint."""
        ckpt_dir = Path(self.config["checkpoint_dir"])
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        path = ckpt_dir / f"adversarial_{tag}.pt"
        torch.save(
            {
                "epoch": epoch,
                "encoder_state": self.encoder.state_dict(),
                "decoder_state": self.decoder.state_dict(),
                "attacker_state": self.attacker.state_dict(),
                "opt_ed_state": self.opt_ed.state_dict(),
                "opt_atk_state": self.opt_atk.state_dict(),
                "config": self.config,
            },
            path,
        )
        logger.info(f"Checkpoint saved: {path}")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def main():
    """Run adversarial training from command line."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--checkpoint", type=str, default=None, help="Resume from checkpoint")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    payload_bits = 160
    image_size = 256

    encoder = WatermarkEncoder(payload_bits=payload_bits, image_size=image_size)
    decoder = WatermarkDecoder(payload_bits=payload_bits, image_size=image_size)
    attacker = WatermarkAttacker()

    # Load pre-trained encoder/decoder if available
    if args.checkpoint and os.path.exists(args.checkpoint):
        ckpt = torch.load(args.checkpoint, map_location="cpu")
        encoder.load_state_dict(ckpt["encoder_state"])
        decoder.load_state_dict(ckpt["decoder_state"])
        logger.info(f"Loaded pre-trained encoder/decoder from {args.checkpoint}")

    config = {"epochs": args.epochs, "batch_size": args.batch_size}
    trainer = AdversarialTrainer(encoder, decoder, attacker, config)

    train_ds = SyntheticWatermarkDataset(num_samples=500, image_size=image_size, payload_bits=payload_bits)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)

    trainer.train(train_loader)


if __name__ == "__main__":
    main()
