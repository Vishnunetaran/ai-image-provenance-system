"""
Configuration module for Provena-FLASK + PROVENA Trinity.

Supports multiple environments: development, production, testing.
Trinity v2 settings control model loading, detection thresholds, and
graceful-degradation behaviour when trained weights are not yet present.
"""

import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Base configuration
# ─────────────────────────────────────────────────────────────────────────────

class Config:
    """Base configuration shared by all environments."""

    # ── Flask core ─────────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", secrets.token_hex(32))

    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_PATH: str = str(BASE_DIR / "data" / "provenance.db")

    # ── Key storage ─────────────────────────────────────────────────────────
    KEYS_DIR: str = str(BASE_DIR / "keys")

    # ── Logging ─────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    # ── API limits ──────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH: int = 32 * 1024 * 1024   # 32 MB
    ALLOWED_EXTENSIONS: set = {"png", "jpg", "jpeg", "webp", "bmp"}
    RATE_LIMIT_REGISTER: int = 10   # requests / minute
    RATE_LIMIT_VERIFY: int = 30     # requests / minute

    # ── Legacy watermark (DWT/DCT) ──────────────────────────────────────────
    WATERMARK_PAYLOAD_BITS: int = 128
    WATERMARK_MIN_PSNR: float = 42.0
    WATERMARK_MIN_SSIM: float = 0.98

    # ── Perceptual hash ─────────────────────────────────────────────────────
    PHASH_THRESHOLD: int = 10   # Hamming distance threshold for a match

    # =========================================================================
    # PROVENA Trinity v2 — Latent watermarking
    # =========================================================================
    # 32-byte key for ChaCha20 pattern generation.
    # In production set TRINITY_SECRET_KEY env var to a stable hex value so
    # that watermarks registered in one process can be extracted in another.
    TRINITY_SECRET_KEY: bytes = bytes.fromhex(
        os.environ.get(
            "TRINITY_SECRET_KEY",
            "4a6f736570687175657a6f6e6e6561626c656b65796865782131323334"  # placeholder
            + "0000"  # pad to 32 bytes
        )[:64]   # truncate if env var is longer
    )

    LATENT_PAYLOAD_BITS: int = 160       # 128-bit UUID + 32-bit timestamp
    LATENT_DETECTION_THRESHOLD: float = 0.5   # correlation threshold [0, 1]
    LATENT_INJECTION_TIMESTEPS: list = [0.5, 0.25, 0.125]  # T/2, T/4, T/8

    # =========================================================================
    # PROVENA Trinity v2 — Pixel watermarking model paths
    # =========================================================================
    # These files are created by Colab training and downloaded to models/.
    MODEL_DIR: str = str(BASE_DIR / "models")

    PIXEL_ENCODER_PATH: str = os.environ.get(
        "PIXEL_ENCODER_PATH",
        str(BASE_DIR / "models" / "pixel_encoder.pth"),
    )
    PIXEL_DECODER_PATH: str = os.environ.get(
        "PIXEL_DECODER_PATH",
        str(BASE_DIR / "models" / "pixel_decoder.pth"),
    )
    CNN_CLASSIFIER_PATH: str = os.environ.get(
        "CNN_CLASSIFIER_PATH",
        str(BASE_DIR / "models" / "cnn_classifier.pth"),
    )
    VIT_DETECTOR_PATH: str = os.environ.get(
        "VIT_DETECTOR_PATH",
        str(BASE_DIR / "models" / "vit_detector.pth"),
    )

    # =========================================================================
    # PROVENA Trinity v2 — Compute device
    # =========================================================================
    # "cuda" | "cuda:0" | "cpu" | "auto"
    # "auto" picks CUDA if available, falls back to CPU.
    DEVICE: str = os.environ.get("DEVICE", "auto")

    # =========================================================================
    # PROVENA Trinity v2 — Detection thresholds
    # =========================================================================
    # Layer 1: Latent watermark correlation score [0, 1]
    WATERMARK_CONFIDENCE_THRESHOLD: float = float(
        os.environ.get("WATERMARK_CONFIDENCE_THRESHOLD", "0.9")
    )
    # Layer 2: Perceptual hash Hamming distance (lower = better match)
    PHASH_MATCH_THRESHOLD: int = int(
        os.environ.get("PHASH_MATCH_THRESHOLD", "15")
    )
    # Layer 3: CNN classifier probability [0, 1]
    CNN_CONFIDENCE_THRESHOLD: float = float(
        os.environ.get("CNN_CONFIDENCE_THRESHOLD", "0.8")
    )
    # Layer 4: ViT detector probability [0, 1]
    VIT_CONFIDENCE_THRESHOLD: float = float(
        os.environ.get("VIT_CONFIDENCE_THRESHOLD", "0.8")
    )
    # Ensemble weighted-sum threshold (VERIFIED if score >= this)
    ENSEMBLE_THRESHOLD: float = float(
        os.environ.get("ENSEMBLE_THRESHOLD", "0.75")
    )

    # Ensemble layer weights — must be positive and sum to 1.0
    ENSEMBLE_WEIGHTS: dict = {
        "latent_watermark": 0.40,
        "perceptual_hash":  0.30,
        "cnn_classifier":   0.20,
        "vit_detector":     0.10,
    }

    # =========================================================================
    # PROVENA Trinity v2 — Graceful degradation
    # =========================================================================
    # When True, the system operates with only the latent watermark layer
    # (no pixel encoder/decoder, no CNN, no ViT) if model files are absent.
    ALLOW_LATENT_ONLY_MODE: bool = True

    # Pixel watermarking model architecture params (must match Colab training)
    PIXEL_PAYLOAD_BITS: int = 160
    PIXEL_IMAGE_SIZE: int = 256


class DevelopmentConfig(Config):
    """Development environment — verbose logging, relaxed thresholds."""

    DEBUG = True
    TESTING = False
    LOG_LEVEL = "DEBUG"
    # Lower thresholds make manual testing easier
    WATERMARK_CONFIDENCE_THRESHOLD = 0.7
    ENSEMBLE_THRESHOLD = 0.60


class ProductionConfig(Config):
    """Production environment — strict secrets, tight thresholds."""

    DEBUG = False
    TESTING = False
    LOG_LEVEL = "WARNING"

    def __init__(self):
        secret_key = os.environ.get("SECRET_KEY")
        if not secret_key:
            raise ValueError(
                "SECRET_KEY must be set in the production environment"
            )
        self.SECRET_KEY = secret_key

        trinity_key = os.environ.get("TRINITY_SECRET_KEY")
        if not trinity_key:
            raise ValueError(
                "TRINITY_SECRET_KEY must be set in the production environment"
            )
        self.TRINITY_SECRET_KEY = bytes.fromhex(trinity_key[:64])


class TestingConfig(Config):
    """Testing environment — in-memory DB, latent-only mode."""

    DEBUG = True
    TESTING = True
    DATABASE_PATH = ":memory:"
    LOG_LEVEL = "DEBUG"
    # Disable strict model requirements in tests
    ALLOW_LATENT_ONLY_MODE = True
    # Lower thresholds so unit tests pass without trained weights
    WATERMARK_CONFIDENCE_THRESHOLD = 0.0
    ENSEMBLE_THRESHOLD = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Config registry
# ─────────────────────────────────────────────────────────────────────────────

config = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
    "default":     DevelopmentConfig,
}
