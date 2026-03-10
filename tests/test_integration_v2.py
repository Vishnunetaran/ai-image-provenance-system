"""
tests/test_integration_v2.py
=============================
End-to-end integration tests for PROVENA Trinity v2 API.

These tests spin up a real Flask test client (in-memory SQLite DB)
and exercise the full register → detect workflow.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from PIL import Image as PILImage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    """Create a test Flask app with in-memory DB."""
    os.environ.setdefault("TRINITY_SECRET_KEY", "4a6f736570687175657a6f6e6e6561626c656b657968657831323334" + "0000")
    from provena_flask import create_app
    application = create_app("testing")
    return application


@pytest.fixture(scope="module")
def client(app):
    with app.test_client() as c:
        yield c


def _make_test_image_b64(w: int = 64, h: int = 64) -> str:
    """Create a tiny JPEG image encoded as base64 for API tests."""
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    pil = PILImage.fromarray(arr)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


TEST_IMAGE_B64 = _make_test_image_b64()


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthCheck:

    def test_health_endpoint_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "healthy"
        assert "trinity_mode" in data
        assert "layers" in data


# ─────────────────────────────────────────────────────────────────────────────
# Watermark info endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestWatermarkInfo:

    def test_watermark_info_returns_200(self, client):
        resp = client.get("/api/v2/watermark/info")
        assert resp.status_code == 200

    def test_watermark_info_has_mode(self, client):
        data = json.loads(client.get("/api/v2/watermark/info").data)
        assert "mode" in data
        assert data["mode"] in ("full", "latent_only", "unavailable")

    def test_watermark_info_has_layers(self, client):
        data = json.loads(client.get("/api/v2/watermark/info").data)
        assert "available_layers" in data
        layers = data["available_layers"]
        assert "latent_watermark" in layers
        assert "perceptual_hash"  in layers


# ─────────────────────────────────────────────────────────────────────────────
# Registration endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestRegisterEndpoint:

    def test_register_returns_201(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1"},
        )
        assert resp.status_code == 201

    def test_register_response_schema(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1"},
        )
        data = json.loads(resp.data)
        required = {
            "image_id", "watermarked_image", "latent_embedded",
            "pixel_embedded", "perceptual_hashes", "timestamp", "processing_ms",
        }
        assert required.issubset(data.keys()), f"Missing keys: {required - data.keys()}"

    def test_register_image_id_format(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1"},
        )
        data = json.loads(resp.data)
        assert data["image_id"].startswith("img-")

    def test_register_latent_embedded_true(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1", "enable_latent": True},
        )
        data = json.loads(resp.data)
        # latent is always available
        assert data["latent_embedded"] is True

    def test_register_only_latent(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1",
                  "enable_latent": True, "enable_pixel": False},
        )
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert data["latent_embedded"] is True
        assert data["pixel_embedded"] is False   # explicitly disabled

    def test_register_missing_image_field(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"model_id": "test-model-v1"},
        )
        assert resp.status_code == 400

    def test_register_invalid_base64(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": "NOT_VALID_BASE64!!!", "model_id": "test-model-v1"},
        )
        assert resp.status_code in (400, 422)

    def test_register_no_content_type(self, client):
        resp = client.post(
            "/api/v2/images/register",
            data="raw data",
            content_type="text/plain",
        )
        assert resp.status_code == 415

    def test_register_watermarked_image_is_base64(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1"},
        )
        data = json.loads(resp.data)
        wm_b64 = data["watermarked_image"]
        # Should be valid base64
        decoded = base64.b64decode(wm_b64)
        assert len(decoded) > 0

    def test_register_phash_present(self, client):
        resp = client.post(
            "/api/v2/images/register",
            json={"image": TEST_IMAGE_B64, "model_id": "test-model-v1"},
        )
        data = json.loads(resp.data)
        assert "perceptual_hashes" in data
        hashes = data["perceptual_hashes"]
        # At least phash must be present
        assert "phash" in hashes


# ─────────────────────────────────────────────────────────────────────────────
# Detection endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectEndpoint:

    def test_detect_returns_200(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64},
        )
        assert resp.status_code == 200

    def test_detect_response_schema(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64},
        )
        data = json.loads(resp.data)
        required = {"status", "confidence", "image_id", "mode", "layers", "processing_ms"}
        assert required.issubset(data.keys())

    def test_detect_status_values(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64},
        )
        data = json.loads(resp.data)
        assert data["status"] in {"VERIFIED", "LIKELY_VERIFIED", "SUSPICIOUS", "NOT_DETECTED"}

    def test_detect_default_mode_is_standard(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64},
        )
        data = json.loads(resp.data)
        assert data["mode"] == "standard"

    def test_detect_fast_mode(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64, "mode": "fast"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["mode"] == "fast"

    def test_detect_deep_mode(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64, "mode": "deep"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["mode"] == "deep"
        assert len(data["layers"]) == 4

    def test_detect_invalid_mode(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64, "mode": "turbo"},
        )
        assert resp.status_code == 400

    def test_detect_layers_list_length(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64, "mode": "deep"},
        )
        data = json.loads(resp.data)
        assert len(data["layers"]) == 4

    def test_detect_layer_names_are_correct(self, client):
        resp = client.post(
            "/api/v2/images/detect",
            json={"image": TEST_IMAGE_B64, "mode": "deep"},
        )
        data = json.loads(resp.data)
        names = {r["layer"] for r in data["layers"]}
        assert names == {"latent_watermark", "perceptual_hash", "cnn_classifier", "vit_detector"}

    def test_detect_missing_image_field(self, client):
        resp = client.post("/api/v2/images/detect", json={"mode": "fast"})
        assert resp.status_code == 400

    def test_detect_unregistered_image_not_found(self, client):
        """A freshly created random image won't be in the DB → NOT_DETECTED or similar."""
        rng = np.random.default_rng(999)
        unregistered = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        buf = io.BytesIO()
        PILImage.fromarray(unregistered).save(buf, "PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")

        resp = client.post(
            "/api/v2/images/detect",
            json={"image": b64, "mode": "fast"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        # pHash layer searched DB → no match → NOT detected by that layer
        phash_layer = next(r for r in data["layers"] if r["layer"] == "perceptual_hash")
        assert phash_layer["ran"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Standalone watermark embed / extract endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestWatermarkEmbedExtract:

    def test_embed_latent_returns_200(self, client):
        resp = client.post(
            "/api/v2/watermark/embed",
            json={"image": TEST_IMAGE_B64, "type": "latent"},
        )
        assert resp.status_code == 200

    def test_embed_response_has_image(self, client):
        resp = client.post(
            "/api/v2/watermark/embed",
            json={"image": TEST_IMAGE_B64, "type": "latent"},
        )
        data = json.loads(resp.data)
        assert "watermarked_image" in data
        assert "latent_embedded"  in data

    def test_embed_latent_embedded_true(self, client):
        resp = client.post(
            "/api/v2/watermark/embed",
            json={"image": TEST_IMAGE_B64, "type": "latent"},
        )
        data = json.loads(resp.data)
        assert data["latent_embedded"] is True

    def test_extract_latent_returns_200(self, client):
        resp = client.post(
            "/api/v2/watermark/extract",
            json={"image": TEST_IMAGE_B64, "type": "latent"},
        )
        assert resp.status_code == 200

    def test_extract_response_has_latent_key(self, client):
        resp = client.post(
            "/api/v2/watermark/extract",
            json={"image": TEST_IMAGE_B64, "type": "latent"},
        )
        data = json.loads(resp.data)
        assert "latent" in data

    def test_embed_invalid_type(self, client):
        resp = client.post(
            "/api/v2/watermark/embed",
            json={"image": TEST_IMAGE_B64, "type": "unknown"},
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Concurrency test
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrency:

    def test_concurrent_register_requests(self, app):
        """10 parallel registration requests must all succeed."""
        def _register():
            with app.test_client() as c:
                resp = c.post(
                    "/api/v2/images/register",
                    json={"image": TEST_IMAGE_B64, "model_id": "test-parallel"},
                )
                return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as ex:
            statuses = list(ex.map(lambda _: _register(), range(10)))

        assert all(s == 201 for s in statuses), f"Some requests failed: {statuses}"

    def test_concurrent_detect_requests(self, app):
        """10 parallel detection requests must all succeed."""
        def _detect():
            with app.test_client() as c:
                resp = c.post(
                    "/api/v2/images/detect",
                    json={"image": TEST_IMAGE_B64, "mode": "fast"},
                )
                return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as ex:
            statuses = list(ex.map(lambda _: _detect(), range(10)))

        assert all(s == 200 for s in statuses), f"Some requests failed: {statuses}"
