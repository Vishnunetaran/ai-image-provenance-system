"""
tests/test_model_loading.py
============================
Tests for model loading, graceful fallback, and latent-only mode.

Run:
    pytest tests/test_model_loading.py -v

All tests pass without any .pth files.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_app_with_cfg(**overrides):
    """Create test Flask app, optionally overriding config values."""
    os.environ.setdefault("TRINITY_SECRET_KEY", "4a6f736570687175657a6f6e6e6561626c656b657968657831323334" + "0000")
    from provena_flask import create_app
    app = create_app("testing")
    for key, val in overrides.items():
        app.config[key] = val
    return app


def _write_dummy_file(path: str):
    """Write a small non-torch-readable file to path."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"NOT_A_TORCH_FILE_XXXXXX")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestModelLoadingFallback:
    """All these tests verify behaviour when model files are absent or broken."""

    def test_app_starts_without_any_pth_files(self):
        """App must start even when no .pth files exist."""
        app = _create_app_with_cfg(
            PIXEL_ENCODER_PATH="/nonexistent/encoder.pth",
            PIXEL_DECODER_PATH="/nonexistent/decoder.pth",
            CNN_CLASSIFIER_PATH="/nonexistent/cnn.pth",
            VIT_DETECTOR_PATH="/nonexistent/vit.pth",
        )
        assert app is not None
        assert app.trinity.mode in ("latent_only", "unavailable")

    def test_latent_extractor_loads_without_files(self):
        """Latent extractor requires no .pth files."""
        app = _create_app_with_cfg()
        # latent_extractor may be None if src module unavailable, but no crash
        assert hasattr(app.trinity, "latent_extractor")

    def test_pixel_encoder_none_when_file_missing(self):
        """pixel_encoder is None when PIXEL_ENCODER_PATH doesn't exist."""
        app = _create_app_with_cfg(PIXEL_ENCODER_PATH="/nonexistent/encoder.pth")
        assert app.trinity.pixel_encoder is None

    def test_pixel_decoder_none_when_file_missing(self):
        """pixel_decoder is None when PIXEL_DECODER_PATH doesn't exist."""
        app = _create_app_with_cfg(PIXEL_DECODER_PATH="/nonexistent/decoder.pth")
        assert app.trinity.pixel_decoder is None

    def test_cnn_classifier_none_when_file_missing(self):
        """cnn_classifier is None when CNN_CLASSIFIER_PATH doesn't exist."""
        app = _create_app_with_cfg(CNN_CLASSIFIER_PATH="/nonexistent/cnn.pth")
        assert app.trinity.cnn_classifier is None

    def test_vit_detector_none_when_file_missing(self):
        """vit_detector is None when VIT_DETECTOR_PATH doesn't exist."""
        app = _create_app_with_cfg(VIT_DETECTOR_PATH="/nonexistent/vit.pth")
        assert app.trinity.vit_detector is None

    def test_pixel_available_false_when_files_missing(self):
        """pixel_available must be False when either encoder or decoder is missing."""
        app = _create_app_with_cfg(
            PIXEL_ENCODER_PATH="/nonexistent/encoder.pth",
            PIXEL_DECODER_PATH="/nonexistent/decoder.pth",
        )
        assert app.trinity.pixel_available is False

    def test_corrupted_model_file_does_not_crash(self):
        """A corrupted .pth file must not crash the app."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_path = os.path.join(tmpdir, "encoder.pth")
            _write_dummy_file(bad_path)   # not a valid PyTorch checkpoint
            app = _create_app_with_cfg(PIXEL_ENCODER_PATH=bad_path)
            # App must start; pixel_encoder should be None
            assert app is not None
            assert app.trinity.pixel_encoder is None

    def test_latent_only_mode_serves_requests(self):
        """In latent-only mode, the registration endpoint should still return 201."""
        import base64, io, numpy as np
        from PIL import Image as PILImage

        app = _create_app_with_cfg(
            PIXEL_ENCODER_PATH="/nonexistent/encoder.pth",
            PIXEL_DECODER_PATH="/nonexistent/decoder.pth",
            CNN_CLASSIFIER_PATH="/nonexistent/cnn.pth",
            VIT_DETECTOR_PATH="/nonexistent/vit.pth",
        )
        rng = np.random.default_rng(1)
        arr = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        buf = io.BytesIO()
        PILImage.fromarray(arr).save(buf, "PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")

        with app.test_client() as c:
            resp = c.post(
                "/api/v2/images/register",
                json={"image": b64, "model_id": "test-latent-only"},
            )
        assert resp.status_code == 201

    def test_latent_only_mode_detect_works(self):
        """In latent-only mode, detect endpoint must return a valid response."""
        import base64, io, numpy as np
        from PIL import Image as PILImage
        import json

        app = _create_app_with_cfg(
            CNN_CLASSIFIER_PATH="/nonexistent/cnn.pth",
            VIT_DETECTOR_PATH="/nonexistent/vit.pth",
        )
        rng = np.random.default_rng(2)
        arr = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        buf = io.BytesIO()
        PILImage.fromarray(arr).save(buf, "PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")

        with app.test_client() as c:
            resp = c.post(
                "/api/v2/images/detect",
                json={"image": b64, "mode": "deep"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        # CNN and ViT should have ran=False
        layers = {r["layer"]: r for r in data["layers"]}
        assert layers["cnn_classifier"]["ran"] is False
        assert layers["vit_detector"]["ran"] is False

    def test_available_layers_dict_always_present(self):
        """TrinityServices.available_layers must always be a dict with 4 keys."""
        from provena_flask import TrinityServices
        svc = TrinityServices()
        layers = svc.available_layers
        assert isinstance(layers, dict)
        assert "latent_watermark" in layers
        assert "perceptual_hash"  in layers
        assert "cnn_classifier"   in layers
        assert "vit_detector"     in layers

    def test_mode_property_returns_string(self):
        """TrinityServices.mode must return a non-empty string."""
        from provena_flask import TrinityServices
        svc = TrinityServices()
        assert isinstance(svc.mode, str)
        assert svc.mode in ("full", "latent_only", "unavailable")

    def test_mode_unavailable_when_nothing_loaded(self):
        """When no services loaded, mode should be 'unavailable'."""
        from provena_flask import TrinityServices
        svc = TrinityServices()    # default: nothing loaded
        svc.latent_available = False
        svc.pixel_available  = False
        svc.cnn_available    = False
        svc.vit_available    = False
        assert svc.mode == "unavailable"

    def test_mode_latent_only_when_only_latent_loaded(self):
        """When only latent is loaded, mode should be 'latent_only'."""
        from provena_flask import TrinityServices
        svc = TrinityServices()
        svc.latent_available = True
        svc.pixel_available  = False
        svc.cnn_available    = False
        svc.vit_available    = False
        assert svc.mode == "latent_only"
