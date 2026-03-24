from __future__ import annotations

import base64
import io
import os
import time

import numpy as np
import pytest
import requests
from PIL import Image

# ---------------------------------------------------------------------------
# Session fixtures (from conftest.py)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def session(api_key):
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {api_key}"
    return s

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def make_image(seed: int = 42, size: tuple = (512, 512)) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (*size, 3), dtype=np.uint8)
    return Image.fromarray(arr)

def image_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()

def register_image(session, api_url, img: Image.Image, model_id: str = "test-model") -> dict:
    resp = session.post(
        f"{api_url}/api/v1/register",
        files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
        data={"model_id": model_id, "creator_did": "did:key:test"},
        timeout=30,
    )
    assert resp.status_code == 201, f"Register failed: {resp.status_code} {resp.text}"
    return resp.json()

def verify_image(session, api_url, img: Image.Image) -> dict:
    resp = session.post(
        f"{api_url}/api/v1/verify",
        files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
        timeout=30,
    )
    assert resp.status_code == 200, f"Verify failed: {resp.status_code} {resp.text}"
    return resp.json()

# ---------------------------------------------------------------------------
# SECTION 1: Registration correctness
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_register_returns_201(self, session, api_url):
        """POST /api/v1/register with a valid PNG returns 201."""
        img = make_image(seed=1)
        resp = session.post(
            f"{api_url}/api/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test-model", "creator_did": "did:key:test"},
            timeout=30,
        )
        assert resp.status_code == 201

    def test_register_response_has_required_fields(self, session, api_url):
        """Response body contains all required fields from PRD spec."""
        img = make_image(seed=2)
        result = register_image(session, api_url, img)
        for field in ("record_id", "manifest_id", "watermarked_image", "manifest_url", "phash", "registered_at"):
            assert field in result, f"Missing field: {field}"

# ---------------------------------------------------------------------------
# SECTION 2: Verification correctness
# ---------------------------------------------------------------------------

class TestVerification:

    def test_verify_watermarked_image_returns_verified(self, session, api_url):
        """Core flow: register → take watermarked image → verify → VERIFIED."""
        img = make_image(seed=10)
        reg = register_image(session, api_url, img)
        wm_img = Image.open(io.BytesIO(base64.b64decode(reg["watermarked_image"])))
        result = verify_image(session, api_url, wm_img)
        assert result["status"] == "VERIFIED", f"Expected VERIFIED, got {result['status']}"

    def test_verify_unregistered_image_returns_unregistered(self, session, api_url):
        """Random image that was never registered returns UNREGISTERED."""
        img = make_image(seed=999_999)
        result = verify_image(session, api_url, img)
        assert result["status"] == "UNREGISTERED", f"Expected UNREGISTERED, got {result['status']}"
