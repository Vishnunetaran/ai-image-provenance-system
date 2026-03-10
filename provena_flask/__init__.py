"""
Provena-FLASK: AI Image Provenance & Forensic Verification Service

Application factory for the Flask application.  Also initialises and
exposes PROVENA Trinity v2 services so that blueprints can reach them
via ``current_app.trinity``.

Trinity service availability follows a graceful-degradation model:

    Mode 1 – Full Trinity (all 4 detection layers)
        Requires: pixel_encoder.pth, pixel_decoder.pth,
                  cnn_classifier.pth, vit_detector.pth

    Mode 2 – Latent-only (layers 1 + 2 always work without .pth files)
        Falls back automatically when model files are missing.
        Controlled by config.ALLOW_LATENT_ONLY_MODE.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Trinity services container
# ─────────────────────────────────────────────────────────────────────────────

class TrinityServices:
    """
    Holds references to all PROVENA Trinity v2 ML services.

    Attributes are set to None when the corresponding model file is absent
    so that callers can do ``if app.trinity.pixel_encoder:`` before using.
    """

    def __init__(self):
        self.secret_key: bytes = b""
        self.device: str = "cpu"

        # Phase 1 – latent watermarking (always available, no .pth needed)
        self.latent_extractor = None    # LatentWatermarkExtractor

        # Phase 2 – pixel watermarking (requires trained weights)
        self.pixel_encoder = None       # WatermarkEncoder
        self.pixel_decoder = None       # WatermarkDecoder

        # Phase 3 – detection ensemble (CNN/ViT require trained weights)
        self.cnn_classifier = None      # CNNClassifier
        self.vit_detector = None        # ViTDetector
        self.trinity_detector = None    # TrinityDetector (facade)

        # Capability flags
        self.latent_available: bool = False
        self.pixel_available: bool = False
        self.cnn_available: bool = False
        self.vit_available: bool = False

    @property
    def available_layers(self) -> Dict[str, bool]:
        return {
            "latent_watermark": self.latent_available,
            "perceptual_hash":  True,           # always available
            "cnn_classifier":   self.cnn_available,
            "vit_detector":     self.vit_available,
        }

    @property
    def mode(self) -> str:
        if self.pixel_available and self.cnn_available and self.vit_available:
            return "full"
        elif self.latent_available:
            return "latent_only"
        return "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app(config_name: str = "development") -> Flask:
    """
    Create and configure the Flask application.

    Args:
        config_name: One of 'development', 'production', 'testing'.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    # ── Load configuration ────────────────────────────────────────────────
    from provena_flask.config import config as config_map
    cfg_cls = config_map.get(config_name, config_map["default"])
    app.config.from_object(cfg_cls)

    # ── Logging ───────────────────────────────────────────────────────────
    _setup_logging(app)

    # ── Database ──────────────────────────────────────────────────────────
    _init_database(app)

    # ── Trinity services ──────────────────────────────────────────────────
    app.trinity = _init_trinity(app)   # type: ignore[attr-defined]

    # ── Blueprints ────────────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Error handlers ────────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Health check ─────────────────────────────────────────────────────
    @app.route("/health")
    def health_check():
        trinity: TrinityServices = app.trinity
        return {
            "status": "healthy",
            "service": "provena-trinity",
            "trinity_mode": trinity.mode,
            "layers": trinity.available_layers,
        }, 200

    app.logger.info(
        "Provena-FLASK initialised in '%s' mode | Trinity mode: %s",
        config_name,
        app.trinity.mode,
    )

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(app: Flask) -> None:
    """Configure structured logging."""
    log_level = app.config.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app.logger.setLevel(getattr(logging, log_level, logging.INFO))


def _init_database(app: Flask) -> None:
    """Ensure the provenance DB exists and is migrated."""
    db_path = app.config.get("DATABASE_PATH", "./data/provenance.db")

    if db_path == ":memory:":
        # Testing mode — don't touch disk
        return

    try:
        from provena_flask.models.provenance import ProvenanceDatabase
        db = ProvenanceDatabase(db_path)
        db.migrate_schema()   # adds Trinity columns if not present
        app.logger.info("Database ready at %s", db_path)
    except Exception as exc:
        app.logger.error("Database initialisation failed: %s", exc)


def _resolve_device(device_cfg: str) -> str:
    """Resolve device string, handling 'auto'."""
    if device_cfg == "auto":
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"
    return device_cfg


def _try_load_model(label: str, loader_fn, **kwargs) -> Optional[Any]:
    """
    Attempt to load a model; return None on any failure so the app
    can start in degraded mode.
    """
    try:
        model = loader_fn(**kwargs)
        logger.info("✅ Loaded %s", label)
        return model
    except FileNotFoundError:
        logger.warning("⚠️  %s weights not found — skipping", label)
    except Exception as exc:
        logger.warning("⚠️  Failed to load %s: %s — skipping", label, exc)
    return None


def _init_trinity(app: Flask) -> TrinityServices:
    """
    Initialise PROVENA Trinity services with graceful degradation.

    Tries to load each model independently.  Missing files or import
    errors are caught and logged, never raised.
    """
    svc = TrinityServices()
    cfg = app.config

    # ── Secret key & device ───────────────────────────────────────────────
    svc.secret_key = cfg.get("TRINITY_SECRET_KEY", b"\x00" * 32)
    svc.device = _resolve_device(cfg.get("DEVICE", "auto"))
    app.logger.info("Trinity compute device: %s", svc.device)

    # ── Layer 1 – Latent watermark extractor ─────────────────────────────
    def _load_latent_extractor():
        from src.watermarking.latent.extractor import LatentWatermarkExtractor
        return LatentWatermarkExtractor(
            secret_key=svc.secret_key,
            detection_threshold=cfg.get("LATENT_DETECTION_THRESHOLD", 0.5),
        )

    svc.latent_extractor = _try_load_model("LatentWatermarkExtractor", _load_latent_extractor)
    svc.latent_available = svc.latent_extractor is not None

    # ── Layer 2 – Pixel encoder ───────────────────────────────────────────
    encoder_path = cfg.get("PIXEL_ENCODER_PATH", "")
    if Path(encoder_path).exists():
        def _load_encoder():
            import torch
            from src.watermarking.pixel.encoder import WatermarkEncoder
            enc = WatermarkEncoder(
                payload_bits=cfg.get("PIXEL_PAYLOAD_BITS", 160),
                image_size=cfg.get("PIXEL_IMAGE_SIZE", 256),
                use_pretrained_backbone=False,
            )
            state = torch.load(encoder_path, map_location=svc.device)
            enc.load_state_dict(state)
            enc.eval()
            return enc.to(svc.device)

        svc.pixel_encoder = _try_load_model("PixelEncoder", _load_encoder)
    else:
        app.logger.warning("Pixel encoder weights not found at %s", encoder_path)

    # ── Layer 2 – Pixel decoder ───────────────────────────────────────────
    decoder_path = cfg.get("PIXEL_DECODER_PATH", "")
    if Path(decoder_path).exists():
        def _load_decoder():
            import torch
            from src.watermarking.pixel.decoder import WatermarkDecoder
            dec = WatermarkDecoder(
                payload_bits=cfg.get("PIXEL_PAYLOAD_BITS", 160),
                image_size=cfg.get("PIXEL_IMAGE_SIZE", 256),
                use_pretrained_backbone=False,
            )
            state = torch.load(decoder_path, map_location=svc.device)
            dec.load_state_dict(state)
            dec.eval()
            return dec.to(svc.device)

        svc.pixel_decoder = _try_load_model("PixelDecoder", _load_decoder)
    else:
        app.logger.warning("Pixel decoder weights not found at %s", decoder_path)

    svc.pixel_available = (svc.pixel_encoder is not None and svc.pixel_decoder is not None)

    # ── Layer 3 – CNN classifier ──────────────────────────────────────────
    cnn_path = cfg.get("CNN_CLASSIFIER_PATH", "")
    if Path(cnn_path).exists():
        def _load_cnn():
            from src.detection.cnn_classifier import CNNClassifier
            return CNNClassifier(
                model_path=cnn_path,
                device=svc.device,
                threshold=cfg.get("CNN_CONFIDENCE_THRESHOLD", 0.8),
            )

        svc.cnn_classifier = _try_load_model("CNNClassifier", _load_cnn)
    else:
        app.logger.warning("CNN classifier weights not found at %s", cnn_path)

    svc.cnn_available = svc.cnn_classifier is not None

    # ── Layer 4 – ViT detector ────────────────────────────────────────────
    vit_path = cfg.get("VIT_DETECTOR_PATH", "")
    if Path(vit_path).exists():
        def _load_vit():
            from src.detection.vit_detector import ViTDetector
            return ViTDetector(
                model_path=vit_path,
                device=svc.device,
                threshold=cfg.get("VIT_CONFIDENCE_THRESHOLD", 0.8),
            )

        svc.vit_detector = _try_load_model("ViTDetector", _load_vit)
    else:
        app.logger.warning("ViT detector weights not found at %s", vit_path)

    svc.vit_available = svc.vit_detector is not None

    # ── Ensemble facade ───────────────────────────────────────────────────
    def _load_ensemble():
        from src.detection.ensemble import TrinityDetector
        return TrinityDetector(
            secret_key=svc.secret_key,
            cnn_model_path=cnn_path if svc.cnn_available else None,
            vit_model_path=vit_path if svc.vit_available else None,
            device=svc.device,
        )

    svc.trinity_detector = _try_load_model("TrinityDetector (ensemble)", _load_ensemble)

    # ── Summary ───────────────────────────────────────────────────────────
    app.logger.info(
        "Trinity services ready | layers: latent=%s pixel=%s cnn=%s vit=%s",
        svc.latent_available,
        svc.pixel_available,
        svc.cnn_available,
        svc.vit_available,
    )

    if not svc.latent_available and not cfg.get("ALLOW_LATENT_ONLY_MODE", True):
        raise RuntimeError(
            "Trinity latent watermarking failed to initialise and "
            "ALLOW_LATENT_ONLY_MODE is False."
        )

    return svc


def _register_blueprints(app: Flask) -> None:
    """Register all Flask blueprints (v1 + v2)."""

    # ── V1 – existing blueprints ──────────────────────────────────────────
    from provena_flask.blueprints.api import api_bp
    from provena_flask.blueprints.registry import registry_bp
    from provena_flask.blueprints.watermark import watermark_bp
    from provena_flask.blueprints.verification import verification_bp
    from provena_flask.blueprints.reports import reports_bp
    from provena_flask.blueprints.demo import demo_bp

    app.register_blueprint(api_bp,          url_prefix="/api/v1")
    app.register_blueprint(registry_bp,     url_prefix="/registry")
    app.register_blueprint(watermark_bp,    url_prefix="/watermark")
    app.register_blueprint(verification_bp, url_prefix="/verification")
    app.register_blueprint(reports_bp,      url_prefix="/reports")
    app.register_blueprint(demo_bp)

    # ── V2 – Trinity blueprints ───────────────────────────────────────────
    try:
        from provena_flask.api_v2.register import register_bp
        from provena_flask.api_v2.detect import detect_bp
        from provena_flask.api_v2.watermark_v2 import watermark_v2_bp

        app.register_blueprint(register_bp,     url_prefix="/api/v2")
        app.register_blueprint(detect_bp,       url_prefix="/api/v2")
        app.register_blueprint(watermark_v2_bp, url_prefix="/api/v2")

        app.logger.info("Trinity v2 blueprints registered at /api/v2")
    except Exception as exc:
        app.logger.error("Failed to register Trinity v2 blueprints: %s", exc)


def _register_error_handlers(app: Flask) -> None:
    """Register application-level error handlers."""
    from flask import jsonify

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify(error="Bad request", message=str(e)), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify(error="Not found", message=str(e)), 404

    @app.errorhandler(413)
    def request_entity_too_large(e):
        max_mb = app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024) // (1024 * 1024)
        return jsonify(
            error="Payload too large",
            message=f"Maximum allowed size is {max_mb} MB",
        ), 413

    @app.errorhandler(500)
    def internal_server_error(e):
        app.logger.exception("Internal server error")
        return jsonify(error="Internal server error", message="An unexpected error occurred"), 500

    # ── Trinity-specific exceptions ───────────────────────────────────────
    try:
        from src.detection.ensemble import DetectionError  # type: ignore
        @app.errorhandler(DetectionError)
        def handle_detection_error(e):
            return jsonify(error="Detection failed", message=str(e)), 422
    except ImportError:
        pass
