"""
C2PA Service — T-020, T-021, T-022, T-025
Generates and verifies C2PA-compliant provenance manifests.

Because the official c2pa-python SDK requires non-trivial native dependencies,
this module implements a lightweight JSON-based manifest that:
  1. Follows the C2PA 2.0 assertion structure.
  2. Is signed with Ed25519 (same key used for the legacy provenance records).
  3. Is embedded into the image EXIF UserComment field via piexif.
  4. Can be extracted and verified from any image with EXIF data intact.

Drop-in replacement with the real c2pa-python library is straightforward
once the SDK is installed: replace `_sign_manifest` / `_verify_signature`
with c2pa signing/verification calls.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import struct
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import piexif
from PIL import Image
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ManifestVerificationResult:
    """Result of C2PA manifest verification."""
    valid: bool
    manifest_id: Optional[str]  = None
    model_id: Optional[str]     = None
    creator_did: Optional[str]  = None
    timestamp: Optional[datetime] = None
    error: Optional[str]        = None


@dataclass
class ManifestData:
    """Internal C2PA-like manifest structure."""
    manifest_id: str
    model_id: str
    creator_did: str
    timestamp: str                        # ISO-8601
    image_hash_sha256: str                # hex digest of raw image bytes
    custom_fields: dict  = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signing key helpers
# ---------------------------------------------------------------------------

def _load_private_key() -> Optional[Ed25519PrivateKey]:
    """Load Ed25519 private key from PEM env var or keys/ directory."""
    pem = os.environ.get("PROVENA_SIGNING_KEY_PEM")
    if pem:
        return serialization.load_pem_private_key(pem.encode(), password=None)  # type: ignore[return-value]

    # Fall back to file-based key
    key_path = os.path.join("keys", "default_private.pem")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)  # type: ignore[return-value]

    logger.warning("No C2PA signing key available; manifests will be unsigned")
    return None


def _load_public_key() -> Optional[Ed25519PublicKey]:
    """Load Ed25519 public key from PEM file."""
    key_path = os.path.join("keys", "default_public.pem")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return serialization.load_pem_public_key(f.read())  # type: ignore[return-value]
    return None


def _sign_manifest(manifest_json: str, private_key: Ed25519PrivateKey) -> str:
    """Sign manifest JSON, return base64-encoded signature."""
    sig = private_key.sign(manifest_json.encode())
    return base64.b64encode(sig).decode()


def _verify_signature(manifest_json: str, signature_b64: str, public_key: Ed25519PublicKey) -> bool:
    """Verify Ed25519 signature over manifest JSON."""
    try:
        sig = base64.b64decode(signature_b64)
        public_key.verify(sig, manifest_json.encode())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Core public functions
# ---------------------------------------------------------------------------

def create_manifest(
    image_bytes: bytes,
    model_id: str,
    creator_did: str,
    timestamp: datetime,
    custom_fields: Optional[dict] = None,
) -> tuple[str, bytes]:
    """
    Create and embed a C2PA-compliant provenance manifest into an image.

    Processes image in-memory only; original pixels are not stored.

    Args:
        image_bytes:   Raw image bytes (PNG, JPEG, WebP).
        model_id:      Identifier of the AI model that generated the image.
        creator_did:   DID of the content creator/registrant.
        timestamp:     UTC datetime of image generation.
        custom_fields: Optional extra fields (e.g., prompt_hash).

    Returns:
        Tuple of:
          - manifest_id (str): UUID for this manifest.
          - signed_image_bytes (bytes): Image with manifest embedded in EXIF.
    """
    manifest_id = str(uuid.uuid4())
    image_hash  = hashlib.sha256(image_bytes).hexdigest()

    manifest = ManifestData(
        manifest_id=manifest_id,
        model_id=model_id,
        creator_did=creator_did,
        timestamp=timestamp.isoformat(),
        image_hash_sha256=image_hash,
        custom_fields=custom_fields or {},
    )

    # Build C2PA-like JSON assertions
    manifest_json = json.dumps(
        {
            "manifest_id": manifest.manifest_id,
            "claim_generator": "Provena/1.0",
            "assertions": [
                {
                    "label": "c2pa.created",
                    "data": {
                        "timestamp": manifest.timestamp,
                        "creator_did": manifest.creator_did,
                    },
                },
                {
                    "label": "c2pa.ai.generativeml.training_and_inference",
                    "data": {
                        "model_id": manifest.model_id,
                    },
                },
                {
                    "label": "c2pa.hash.data",
                    "data": {
                        "alg": "sha256",
                        "hash": manifest.image_hash_sha256,
                    },
                },
            ],
            "custom_fields": manifest.custom_fields,
        },
        sort_keys=True,
    )

    # Sign manifest
    private_key = _load_private_key()
    signature_b64 = ""
    if private_key:
        signature_b64 = _sign_manifest(manifest_json, private_key)
    else:
        logger.warning("Manifest created without signature (no signing key)")

    # Bundle: manifest + signature
    bundle = json.dumps(
        {"manifest": json.loads(manifest_json), "signature": signature_b64},
        sort_keys=True,
    )

    # Embed into EXIF UserComment
    signed_image_bytes = _embed_exif(image_bytes, bundle)

    logger.info("C2PA manifest created: %s", manifest_id)
    return manifest_id, signed_image_bytes


def verify_manifest(image_bytes: bytes) -> ManifestVerificationResult:
    """
    Extract and verify the C2PA manifest from an image's EXIF data.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        ManifestVerificationResult with verification status and extracted fields.
    """
    bundle_str = _extract_exif(image_bytes)
    if not bundle_str:
        return ManifestVerificationResult(valid=False, error="NO_MANIFEST")

    try:
        bundle = json.loads(bundle_str)
    except json.JSONDecodeError:
        return ManifestVerificationResult(valid=False, error="MANIFEST_CORRUPT")

    manifest_dict  = bundle.get("manifest", {})
    signature_b64  = bundle.get("signature", "")
    manifest_json  = json.dumps(manifest_dict, sort_keys=True)

    # Verify signature
    public_key = _load_public_key()
    if not public_key:
        # No public key to verify with; treat as unverifiable
        return ManifestVerificationResult(valid=False, error="NO_PUBLIC_KEY")

    if not _verify_signature(manifest_json, signature_b64, public_key):
        return ManifestVerificationResult(
            valid=False,
            manifest_id=manifest_dict.get("manifest_id"),
            error="SIGNATURE_INVALID",
        )

    # Parse assertions
    model_id    = None
    creator_did = None
    timestamp   = None

    for assertion in manifest_dict.get("assertions", []):
        label = assertion.get("label", "")
        data  = assertion.get("data", {})
        if label == "c2pa.created":
            creator_did = data.get("creator_did")
            ts_str = data.get("timestamp")
            if ts_str:
                try:
                    timestamp = datetime.fromisoformat(ts_str)
                except ValueError:
                    pass
        elif label == "c2pa.ai.generativeml.training_and_inference":
            model_id = data.get("model_id")

    logger.info("C2PA manifest verified: %s", manifest_dict.get("manifest_id"))
    return ManifestVerificationResult(
        valid=True,
        manifest_id=manifest_dict.get("manifest_id"),
        model_id=model_id,
        creator_did=creator_did,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# EXIF embedding / extraction helpers
# ---------------------------------------------------------------------------

def _embed_exif(image_bytes: bytes, payload: str) -> bytes:
    """Embed a string payload into EXIF UserComment field."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Load or create EXIF dict
        try:
            exif_dict = piexif.load(img.info.get("exif", b""))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

        # Encode as UTF-8 with piexif UserComment prefix
        user_comment = b"ASCII\x00\x00\x00" + payload.encode("ascii", errors="replace")
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment

        exif_bytes = piexif.dump(exif_dict)
        output = io.BytesIO()
        fmt = img.format or "JPEG"
        if fmt == "PNG":
            # PNG doesn't store EXIF natively; embed in JPEG for compatibility
            img = img.convert("RGB")
            img.save(output, format="JPEG", exif=exif_bytes, quality=95)
        else:
            img.save(output, format=fmt, exif=exif_bytes, quality=95)
        return output.getvalue()
    except Exception as exc:
        logger.warning("EXIF embedding failed: %s — returning original bytes", exc)
        return image_bytes


def _extract_exif(image_bytes: bytes) -> Optional[str]:
    """Extract the UserComment string from EXIF data."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif_bytes = img.info.get("exif")
        if not exif_bytes:
            return None
        exif_dict = piexif.load(exif_bytes)
        raw = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment)
        if not raw:
            return None
        # Strip ASCII prefix if present
        if raw.startswith(b"ASCII\x00\x00\x00"):
            raw = raw[8:]
        elif raw.startswith(b"UNICODE\x00"):
            raw = raw[8:]
        return raw.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.debug("EXIF extraction failed: %s", exc)
        return None
