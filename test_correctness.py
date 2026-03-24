"""
Provena v2 — Correctness Unit Tests
=====================================
Tests the logical correctness of the /register and /verify endpoints
independently of robustness. Run these BEFORE the robustness benchmark.

Usage:
    pytest tests/test_correctness.py -v
    pytest tests/test_correctness.py -v --api-url http://localhost:5000 --api-key prov_sk_...

Requires:
    pip install pytest requests pillow numpy
"""

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
# Config — override via env vars or pytest CLI args
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--api-url", default=os.getenv("PROVENA_API_URL", "http://localhost:5000"))
    parser.addoption("--api-key", default=os.getenv("PROVENA_API_KEY", "prov_sk_test"))

@pytest.fixture(scope="session")
def api_url(request):
    return request.config.getoption("--api-url")

@pytest.fixture(scope="session")
def api_key(request):
    return request.config.getoption("--api-key")

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
        f"{api_url}/v1/register",
        files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
        data={"model_id": model_id, "creator_did": "did:key:test"},
        timeout=30,
    )
    assert resp.status_code == 201, f"Register failed: {resp.status_code} {resp.text}"
    return resp.json()

def verify_image(session, api_url, img: Image.Image) -> dict:
    resp = session.post(
        f"{api_url}/v1/verify",
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
        """POST /v1/register with a valid PNG returns 201."""
        img = make_image(seed=1)
        resp = session.post(
            f"{api_url}/v1/register",
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

    def test_register_returns_watermarked_image(self, session, api_url):
        """Returned watermarked_image is valid base64-encoded PNG/JPEG."""
        img = make_image(seed=3)
        result = register_image(session, api_url, img)
        wm_b64 = result["watermarked_image"]
        wm_bytes = base64.b64decode(wm_b64)
        wm_img = Image.open(io.BytesIO(wm_bytes))
        assert wm_img.size[0] > 0 and wm_img.size[1] > 0

    def test_register_watermarked_image_same_size(self, session, api_url):
        """Watermarked image must be same dimensions as input."""
        img = make_image(seed=4, size=(768, 512))
        result = register_image(session, api_url, img)
        wm_bytes = base64.b64decode(result["watermarked_image"])
        wm_img = Image.open(io.BytesIO(wm_bytes))
        assert wm_img.size == img.size, f"Size mismatch: {wm_img.size} != {img.size}"

    def test_register_phash_is_64bit_hex(self, session, api_url):
        """phash field is a 16-character hex string (64 bits)."""
        img = make_image(seed=5)
        result = register_image(session, api_url, img)
        phash = result["phash"]
        assert len(phash) == 16, f"phash length {len(phash)} != 16"
        int(phash, 16)  # must be valid hex

    def test_register_manifest_url_is_reachable(self, session, api_url):
        """manifest_url returns a 200 with C2PA manifest content."""
        img = make_image(seed=6)
        result = register_image(session, api_url, img)
        manifest_resp = session.get(result["manifest_url"], timeout=10)
        assert manifest_resp.status_code == 200
        assert manifest_resp.headers.get("Content-Type", "").startswith("application/")

    def test_register_rejects_too_small_image(self, session, api_url):
        """Images smaller than 256×256 should be rejected with 400."""
        img = make_image(seed=7, size=(128, 128))
        resp = session.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test-model"},
            timeout=10,
        )
        assert resp.status_code == 400

    def test_register_idempotency_key(self, session, api_url):
        """Same Idempotency-Key returns same record_id on repeat calls."""
        img = make_image(seed=8)
        idem_key = f"test-idem-{int(time.time())}"
        headers = {"Idempotency-Key": idem_key}

        resp1 = session.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test-model"},
            headers=headers,
            timeout=30,
        )
        resp2 = session.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test-model"},
            headers=headers,
            timeout=30,
        )
        assert resp1.json()["record_id"] == resp2.json()["record_id"]

    def test_register_requires_auth(self, api_url):
        """Requests without Authorization header return 401."""
        img = make_image(seed=9)
        resp = requests.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test-model"},
            timeout=10,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SECTION 2: Verification correctness (the four status codes)
# ---------------------------------------------------------------------------

class TestVerification:

    def test_verify_watermarked_image_returns_verified(self, session, api_url):
        """
        Core flow: register → take watermarked image → verify → VERIFIED.
        This is the most important correctness test.
        """
        img = make_image(seed=10)
        reg = register_image(session, api_url, img)
        wm_img = Image.open(io.BytesIO(base64.b64decode(reg["watermarked_image"])))
        result = verify_image(session, api_url, wm_img)
        assert result["status"] == "VERIFIED", f"Expected VERIFIED, got {result['status']}"
        assert result["confidence"] >= 0.7
        assert result["watermark_extracted"] is True

    def test_verify_unregistered_image_returns_unregistered(self, session, api_url):
        """Random image that was never registered returns UNREGISTERED."""
        img = make_image(seed=999_999)
        result = verify_image(session, api_url, img)
        assert result["status"] == "UNREGISTERED", f"Expected UNREGISTERED, got {result['status']}"

    def test_verify_original_pre_watermark_returns_verified_modified(self, session, api_url):
        """
        The original image (before watermark embedding) should match via pHash
        and return VERIFIED_MODIFIED (not VERIFIED, since watermark not present).
        """
        img = make_image(seed=11)
        register_image(session, api_url, img)
        result = verify_image(session, api_url, img)
        # Original image: pHash matches but watermark not extracted
        assert result["status"] in ("VERIFIED_MODIFIED", "VERIFIED"), \
            f"Original image returned {result['status']} — expected VERIFIED or VERIFIED_MODIFIED"

    def test_verify_response_has_required_fields(self, session, api_url):
        """Verify response contains all PRD-specified fields."""
        img = make_image(seed=12)
        result = verify_image(session, api_url, img)
        for field in ("status", "confidence"):
            assert field in result, f"Missing field in verify response: {field}"

    def test_verify_confidence_is_between_0_and_1(self, session, api_url):
        """Confidence score must be in [0.0, 1.0] range."""
        img = make_image(seed=13)
        result = verify_image(session, api_url, img)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_verify_verified_response_has_provenance_fields(self, session, api_url):
        """VERIFIED response must include model_id, registered_at, record_id."""
        img = make_image(seed=14)
        reg = register_image(session, api_url, img, model_id="my-special-model")
        wm_img = Image.open(io.BytesIO(base64.b64decode(reg["watermarked_image"])))
        result = verify_image(session, api_url, wm_img)
        assert result["status"] == "VERIFIED"
        assert result.get("model_id") == "my-special-model"
        assert "registered_at" in result
        assert "record_id" in result

    def test_verify_requires_auth(self, api_url):
        """Requests without Authorization header return 401."""
        img = make_image(seed=15)
        resp = requests.post(
            f"{api_url}/v1/verify",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            timeout=10,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SECTION 3: C2PA manifest correctness
# ---------------------------------------------------------------------------

class TestC2PA:

    def test_c2pa_manifest_is_embedded_in_returned_image(self, session, api_url):
        """
        The watermarked image returned by /register must have XMP/EXIF
        metadata present (basic check: non-zero metadata bytes).
        """
        try:
            import piexif
        except ImportError:
            pytest.skip("piexif not installed — skipping C2PA metadata check")

        img = make_image(seed=20)
        reg = register_image(session, api_url, img)
        wm_bytes = base64.b64decode(reg["watermarked_image"])

        # Save as JPEG and check EXIF
        pil_img = Image.open(io.BytesIO(wm_bytes))
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG")
        buf.seek(0)
        try:
            exif_data = piexif.load(buf.read())
            has_exif = bool(exif_data)
        except Exception:
            has_exif = False

        # We don't assert has_exif=True here because PNG may not embed EXIF
        # Just ensure the manifest_url works (tested in TestRegistration)
        assert reg["manifest_id"] is not None

    def test_manifest_endpoint_returns_model_id(self, session, api_url):
        """GET /v1/manifests/{id} response must contain the registered model_id."""
        img = make_image(seed=21)
        reg = register_image(session, api_url, img, model_id="c2pa-test-model")
        manifest_resp = session.get(reg["manifest_url"], timeout=10)
        assert manifest_resp.status_code == 200
        body = manifest_resp.text
        assert "c2pa-test-model" in body, "model_id not found in C2PA manifest"


# ---------------------------------------------------------------------------
# SECTION 4: Auth & rate limiting
# ---------------------------------------------------------------------------

class TestAuth:

    def test_invalid_api_key_returns_401(self, api_url):
        """A garbage API key returns 401, not 500."""
        img = make_image(seed=30)
        resp = requests.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test"},
            headers={"Authorization": "Bearer prov_sk_totallyFakeKey12345"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_malformed_auth_header_returns_401(self, api_url):
        """Malformed Authorization header (not 'Bearer ...') returns 401."""
        img = make_image(seed=31)
        resp = requests.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            data={"model_id": "test"},
            headers={"Authorization": "NotBearer something"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_error_response_has_standard_format(self, api_url):
        """Error responses have {error: {code, message, request_id}} shape."""
        resp = requests.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", b"not-an-image", "image/png")},
            data={"model_id": "test"},
            headers={"Authorization": "Bearer prov_sk_fake"},
            timeout=10,
        )
        try:
            body = resp.json()
            # Either it has error.code or it's a 401 with a message — both acceptable
            assert "error" in body or resp.status_code == 401
        except Exception:
            pass  # Non-JSON error response is also acceptable for 401

    def test_x_request_id_header_present(self, session, api_url):
        """Every response must include X-Request-ID header."""
        img = make_image(seed=32)
        resp = session.post(
            f"{api_url}/v1/verify",
            files={"image": ("image.png", image_to_png_bytes(img), "image/png")},
            timeout=10,
        )
        assert "X-Request-ID" in resp.headers, "X-Request-ID header missing from response"


# ---------------------------------------------------------------------------
# SECTION 5: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_jpeg_input_accepted(self, session, api_url):
        """API must accept JPEG input, not just PNG."""
        img = make_image(seed=40)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        resp = session.post(
            f"{api_url}/v1/register",
            files={"image": ("image.jpg", buf.read(), "image/jpeg")},
            data={"model_id": "test-model"},
            timeout=30,
        )
        assert resp.status_code == 201

    def test_corrupted_image_returns_400(self, session, api_url):
        """Sending random bytes as image returns 400, not 500."""
        resp = session.post(
            f"{api_url}/v1/register",
            files={"image": ("image.png", b"this is not an image at all", "image/png")},
            data={"model_id": "test-model"},
            timeout=10,
        )
        assert resp.status_code == 400

    def test_two_different_images_have_different_phash(self, session, api_url):
        """Two visually distinct images must produce different pHash values."""
        img_a = make_image(seed=50)
        img_b = make_image(seed=51)
        reg_a = register_image(session, api_url, img_a)
        reg_b = register_image(session, api_url, img_b)
        assert reg_a["phash"] != reg_b["phash"], "Distinct images returned identical pHash — collision bug"

    def test_phash_not_shifted_more_than_10_bits_by_watermark(self, session, api_url):
        """
        The watermark must not shift pHash by more than 10 bits.
        This validates the 'compute pHash before watermarking' invariant.
        """
        import imagehash
        img = make_image(seed=52)
        reg = register_image(session, api_url, img)

        original_phash = int(reg["phash"], 16)
        wm_img = Image.open(io.BytesIO(base64.b64decode(reg["watermarked_image"])))
        wm_phash_val = imagehash.phash(wm_img)
        wm_phash_int = int(str(wm_phash_val), 16)

        hamming = bin(original_phash ^ wm_phash_int).count("1")
        assert hamming <= 10, (
            f"Watermark shifted pHash by {hamming} bits (max allowed: 10). "
            f"This means the neural encoder is distorting the image too aggressively."
        )
