"""
Provena REST API — v1 endpoints.
T-046, T-047, T-048, T-049, T-050, T-051, T-052, T-053, T-054

Implements the three-path verification flow:
  Path 1 — C2PA fast path   (EXIF manifest + Ed25519 signature)
  Path 2 — Neural extraction (256-bit payload decode + ECC correction)
  Path 3 — pHash fallback    (Hamming-distance search ≤ 10 bits)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import secrets
import struct
import uuid
from datetime import datetime, timezone
from typing import Optional

import imagehash
from flask import Blueprint, g, jsonify, request
from PIL import Image

from provena_flask.auth import require_api_key, create_key_record, revoke_key
from provena_flask.errors import bad_request, internal_error, not_found, error_response
from provena_flask.models import db as db_module
from provena_flask.services import c2pa_service, neural_watermark_service
from provena_flask.services import payload_codec

logger = logging.getLogger(__name__)

api_bp = Blueprint("api_v1", __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 10 MB
MIN_DIM         = 256                 # px
NEURAL_CONFIDENCE_THRESHOLD = 0.70
HAMMING_MAX     = 10                  # NEVER increase above 10


# ---------------------------------------------------------------------------
# Status endpoint (unauthenticated)
# ---------------------------------------------------------------------------

@api_bp.route("/status", methods=["GET"])
def api_status():
    """Health-check endpoint."""
    return jsonify({"status": "operational", "version": "v1", "service": "provena-api"}), 200


# ---------------------------------------------------------------------------
# T-044: API key creation
# ---------------------------------------------------------------------------

@api_bp.route("/keys", methods=["POST"])
def create_key():
    """
    POST /v1/keys — Create a new API key.

    Request JSON:
        { "org_name": "Acme Corp", "tier": "free" }

    Returns the plaintext key ONCE. It is never returned again.
    """
    data = request.get_json(silent=True) or {}
    org_name = data.get("org_name", "unnamed")
    tier     = data.get("tier", "free")

    if tier not in ("free", "starter", "growth", "enterprise"):
        return bad_request("Invalid tier; must be free|starter|growth|enterprise", "INVALID_TIER")

    try:
        record = create_key_record(org_name=org_name, tier=tier)
    except Exception as exc:
        logger.error("Key creation failed: %s", exc)
        return internal_error("Failed to create API key")

    return jsonify(record), 201


# ---------------------------------------------------------------------------
# T-045: API key revocation
# ---------------------------------------------------------------------------

@api_bp.route("/keys/<key_id>", methods=["DELETE"])
@require_api_key(kind="verify")
def delete_key(key_id: str):
    """DELETE /v1/keys/{id} — Revoke an API key (soft delete)."""
    success = revoke_key(key_id)
    if not success:
        return not_found(f"Key {key_id} not found")
    return jsonify({"revoked": True, "key_id": key_id}), 200


# ---------------------------------------------------------------------------
# T-046, T-047, T-048: POST /v1/register
# ---------------------------------------------------------------------------

@api_bp.route("/register", methods=["POST"])
@require_api_key(kind="register")
def register_image():
    """
    Register an AI-generated image with neural watermark + C2PA manifest.
    """
    import traceback
    try:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        print(f"\n╔══════════════════ REGISTRATION START ══════════════════╗")
        print(f"║ Request ID: {request_id[:36]:<35} ║")
        print(f"║ API Key ID: {g.api_key['id']:<35} ║")
        print(f"╟────────────────────────────────────────────────────────╢")

        # --- Idempotency (T-048) ---
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            cached = _idempotency_get(idempotency_key)
            if cached:
                print(f"║ [CACHE] Idempotency Hit: {idempotency_key[:25]}... ║")
                return jsonify(cached), 200

        # --- Parse image bytes ---
        image_bytes, parse_error = _parse_image_from_request()
        if parse_error:
            print(f"║ [ERROR] Parse failure: {parse_error[:30]:<25} ║")
            print(f"╚════════════════════════ FAULT ═════════════════════════╝")
            return bad_request(parse_error, "INVALID_IMAGE")

        # --- Parse metadata ---
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        model_id    = (data.get("model_id") or "").strip()
        creator_did = (data.get("creator_did") or "").strip()
        print(f"║ Metadata: {model_id[:15]:<15} | {creator_did[:23]:<23} ║")

        custom_str  = data.get("custom_fields", "{}")
        try:
            custom_fields = json.loads(custom_str) if isinstance(custom_str, str) else custom_str
        except json.JSONDecodeError:
            custom_fields = {}

        if not model_id:
            return bad_request("model_id is required", "MISSING_FIELD")

        timestamp = datetime.now(timezone.utc)

        # --- Validate & open image ---
        try:
            img = _open_image_safely(image_bytes)
            w, h = img.size
            print(f"║ Image Details: {len(image_bytes)/1024:.1f} KB | {w}x{h} px          ║")
        except ValueError as exc:
            print(f"║ [ERROR] Image invalid: {str(exc)[:25]:<25}  ║")
            return bad_request(str(exc), "INVALID_IMAGE")

        # --- Compute pHash on ORIGINAL image ---
        phash_int, phash_hex = _compute_phash(img)
        print(f"║ pHash (Fingerprint): {phash_hex:<25} ║")
        print(f"╟────────────────────────────────────────────────────────╢")

        # --- Persist record (BEFORE watermarking, so we have record_id) ---
        record_id = str(uuid.uuid4())
        now_iso = timestamp.isoformat()
        manifest_id = str(uuid.uuid4())  # Pre-generate; updated if C2PA succeeds
        # Convert UUID to 32-bit int for compact payload
        record_id_int = int(record_id.replace('-', ''), 16) % (2**32)

        # ── Encode 48-bit neural payload (4B record_id + 2B ECC) ─────────────
        neural_payload = payload_codec.encode(record_id_int)
        payload_hex = neural_payload.hex()
        print(f"║ Neural Payload: {payload_hex} ({len(neural_payload)}B)          ║")

        try:
            print(f"╟────────────────────────────────────────────────────────╢")
            print(f"║ Database: Saving record {record_id[:8]}...             ║")
            with db_module.get_connection() as conn:
                _insert_record(conn, {
                    "id":             record_id,
                    "manifest_id":    manifest_id,
                    "image_phash_int": phash_int,
                    "image_phash_hex": phash_hex,
                    "model_id":       model_id,
                    "creator_did":    creator_did or "did:provena:anonymous",
                    "registered_at":  now_iso,
                    "payload_hex":    payload_hex,
                    "api_key_id":     g.api_key["id"],
                    "custom_fields":  json.dumps(custom_fields),
                    "created_at":     now_iso,
                })
                conn.execute(
                    "INSERT INTO manifests (id, c2pa_json, created_at) VALUES (?, ?, ?)",
                    (manifest_id, json.dumps({"model_id": model_id, "creator": creator_did}), now_iso)
                )
            db_module.cache_invalidate_phash(phash_int)
            print(f"║   Status: COMMITTED                                    ║")
        except Exception as exc:
            print(f"║   Status: SQL_ERROR ({str(exc)[:25]}...)    ║")
            logger.error("DB insert error: %s", exc)
            return internal_error("Failed to persist provenance record")

        # ── Neural watermark embedding (48-bit payload) ──────────────────────
        print(f"╟────────────────────────────────────────────────────────╢")
        print(f"║ Neural Path: Embedding TrustMark (48-bit payload)...   ║")
        try:
            watermarked_img = neural_watermark_service.embed(img, neural_payload)
            print(f"║   Embedding: SUCCESS                                   ║")
        except Exception as exc:
            print(f"║   Embedding: WARNING ({str(exc)[:25]}...) ║")
            logger.warning("Neural embedding error: %s", exc)
            watermarked_img = img.copy()

        # --- C2PA manifest generation ---
        try:
            print(f"╟────────────────────────────────────────────────────────╢")
            real_manifest_id, signed_image_bytes = c2pa_service.create_manifest(
                image_bytes=image_bytes,
                model_id=model_id,
                creator_did=creator_did or "did:provena:anonymous",
                timestamp=timestamp,
                custom_fields=custom_fields
            )
            # Update manifest_id if C2PA generated a different one
            if real_manifest_id != manifest_id:
                with db_module.get_connection() as conn:
                    conn.execute(
                        "UPDATE provenance_records SET manifest_id = ? WHERE id = ?",
                        (real_manifest_id, record_id)
                    )
                    conn.execute(
                        "UPDATE manifests SET id = ? WHERE id = ?",
                        (real_manifest_id, manifest_id)
                    )
                manifest_id = real_manifest_id
            print(f"║ C2PA Manifest ID: {manifest_id[:36]:<36} ║")
            print(f"║   Injection: SUCCESS                                   ║")
        except Exception as exc:
            print(f"║   Injection: FAILED ({str(exc)[:25]}...)    ║")
            logger.error("C2PA embedding failed: %s", exc)
            buf = io.BytesIO()
            watermarked_img.save(buf, format="PNG")
            signed_image_bytes = buf.getvalue()

        # Response
        wm_buf = io.BytesIO()
        watermarked_img.save(wm_buf, format="PNG")
        watermarked_b64 = base64.b64encode(wm_buf.getvalue()).decode()

        response_body = {
            "record_id":        record_id,
            "manifest_id":      manifest_id,
            "watermarked_image": watermarked_b64,
            "manifest_url":     f"/v1/manifests/{manifest_id}",
            "phash":            phash_hex,
            "registered_at":    now_iso,
            "model_id":         model_id,
            "creator_did":      creator_did,
            "request_id":       request_id,
        }
        if idempotency_key:
            _idempotency_set(idempotency_key, response_body)

        print(f"╚══════════════════ REGISTRATION COMPLETE ════════════════╝\n")
        return jsonify(response_body), 201

    except Exception:
        err = traceback.format_exc()
        print(f"║ [FATAL] {err.splitlines()[-1][:40]:<40} ║")
        print(f"╚════════════════════════ ERROR ═════════════════════════╝")
        logger.error("Fatal error in register_image:\n%s", err)
        return internal_error("A fatal server error occurred.")


# ---------------------------------------------------------------------------
# T-049, T-050, T-051: POST /v1/verify
# ---------------------------------------------------------------------------

@api_bp.route("/verify", methods=["POST"])
@require_api_key(kind="verify")
def verify_image():
    """
    Verify image provenance using three-path decision tree.
    """
    import traceback
    try:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        print(f"\n╔═══════════════════ VERIFICATION START ══════════════════╗")
        print(f"║ Request ID: {request_id[:36]:<35} ║")

        image_bytes, parse_error = _parse_image_from_request()
        if parse_error:
            print(f"║ [ERROR] Parse failure: {parse_error[:30]:<25} ║")
            return bad_request(parse_error, "INVALID_IMAGE")

        try:
            img = _open_image_safely(image_bytes)
            print(f"║ Image: {len(image_bytes)/1024:.1f} KB | {img.size[0]}x{img.size[1]} px              ║")
        except ValueError as exc:
            print(f"║ [ERROR] Image invalid: {str(exc)[:25]:<25}  ║")
            return bad_request(str(exc), "INVALID_IMAGE")

        status          = "UNREGISTERED"
        confidence      = 1.0
        record: Optional[dict] = None
        watermark_extracted = False
        c2pa_valid      = False
        hamming_dist    = None

        # ── Path 1: C2PA manifest ────────────────────────────────────────────────
        print(f"╟────────────────────────────────────────────────────────╢")
        print(f"║ Path 1: Checking Cryptographic Manifest (C2PA)...      ║")
        try:
            manifest_result = c2pa_service.verify_manifest(image_bytes)

            if manifest_result.error == "SIGNATURE_INVALID":
                print(f"║   Result: TAMPERED (Signature Invalid)                 ║")
                status     = "TAMPERED"
                confidence = 0.99
                c2pa_valid = False
                return _build_verify_response(
                    status, confidence, record, watermark_extracted, c2pa_valid, hamming_dist, request_id,
                    match_type="c2pa"
                )

            if manifest_result.valid:
                print(f"║   Result: VALID ({manifest_result.manifest_id[:8]}...)          ║")
                record = _get_record_by_manifest(manifest_result.manifest_id)
                if record:
                    print(f"║   Match:  FOUND in Database                            ║")
                    status              = "VERIFIED"
                    confidence          = 0.99
                    watermark_extracted = True
                    c2pa_valid          = True
                    hamming_dist        = 0
                    _write_audit_log(record.get("id"), g.api_key["id"], status, confidence, None, request_id)
                    print(f"╚══════════════════════ VERIFIED ════════════════════════╝\n")
                    return _build_verify_response(
                        status, confidence, record, watermark_extracted, c2pa_valid, hamming_dist, request_id,
                        match_type="c2pa"
                    )
            else:
                print(f"║   Result: NOT FOUND                                    ║")
        except Exception as exc:
            # C2PA check failed entirely — log and continue to neural path
            print(f"║   Result: C2PA ERROR ({str(exc)[:25]}...)     ║")
            logger.warning("C2PA check error: %s", exc)

        # ── Path 2: Neural watermark extraction (TrustMark 48-bit) ────────────
        print(f"╟────────────────────────────────────────────────────────╢")
        print(f"║ Path 2: Probing Neural Watermark (TrustMark)...        ║")
        try:
            payload_bytes, neural_confidence = neural_watermark_service.extract(img)
            print(f"║   Extraction: SUCCESS (Conf: {neural_confidence:.3f})              ║")
        except Exception as exc:
            print(f"║   Extraction: FAILED ({str(exc)[:20]}...)        ║")
            logger.warning("Neural extraction error: %s", exc)
            payload_bytes, neural_confidence = b"", 0.0

        if neural_confidence >= NEURAL_CONFIDENCE_THRESHOLD and len(payload_bytes) == 6:
            try:
                record_id_int = payload_codec.decode(payload_bytes)
                print(f"║   Correction: RS-ECC SUCCESS (ID: {record_id_int})         ║")

                record = _get_record_by_payload(payload_bytes.hex())

                if record:
                    print(f"║   Match:      FOUND in Database                        ║")
                    status              = "VERIFIED"
                    confidence          = neural_confidence
                    watermark_extracted = True
                    hamming_dist        = 0
                    _write_audit_log(record.get("id"), g.api_key["id"], status, confidence, 0, request_id)
                    print(f"╚══════════════════════ VERIFIED ════════════════════════╝\n")
                    return _build_verify_response(
                        status, confidence, record, watermark_extracted, c2pa_valid, hamming_dist, request_id,
                        match_type="neural"
                    )
                else:
                    print(f"║   Match:      NOT FOUND (Stale record?)                ║")
            except payload_codec.PayloadDecodeError as exc:
                print(f"║   Correction: RS-ECC FAILED                            ║")
                logger.debug("ECC decode failed (falling through to pHash): %s", exc)
            except Exception as exc:
                print(f"║   Error:      {str(exc)[:30]:<30} ║")
                logger.warning("Neural path error: %s", exc)
        elif neural_confidence >= NEURAL_CONFIDENCE_THRESHOLD:
            print(f"║   Payload size mismatch: {len(payload_bytes)}B (expected 6B)       ║")
        else:
            print(f"║   Threshold:  Insufficient (Below {NEURAL_CONFIDENCE_THRESHOLD})            ║")

        # ── Path 3: pHash Hamming-distance fallback ───────────────────────────────
        print(f"╟────────────────────────────────────────────────────────╢")
        print(f"║ Path 3: Calculating Perceptual Fingerprint (pHash)...  ║")
        phash_int, phash_hex = _compute_phash(img)
        print(f"║   Query Hash: {phash_hex:<41}║")

        # Tight search: Hamming distance <= 10
        matches = db_module.find_by_hamming(phash_int, max_distance=HAMMING_MAX)

        if matches:
            best         = matches[0]
            hamming_dist = best.get("hamming_dist", 0)
            record       = best
            print(f"║   Result:     MATCH FOUND (Dist: {hamming_dist})                  ║")
            print(f"║   Match ID:   {record.get('id')[:28]:<28} ║")

            status     = "VERIFIED_MODIFIED"
            confidence = max(0.0, 0.70 - hamming_dist * 0.03)
            _write_audit_log(record.get("id"), g.api_key["id"], status, confidence, hamming_dist, request_id)
            print(f"╚══════════════════ VERIFIED (MODIFIED) ═════════════════╝\n")
            return _build_verify_response(
                status, confidence, record, watermark_extracted, c2pa_valid, hamming_dist, request_id,
                match_type="phash"
            )
        else:
            print(f"║   Result:     NO MATCH (Tight ≤{HAMMING_MAX})                    ║")

        # Wide pHash fallback — catches cropped/heavily edited images
        # Higher false positive risk — confidence is reduced accordingly
        # Do not widen beyond 20 bits (random 64-bit hashes have mean distance ~32)
        HAMMING_WIDE = 20
        print(f"║   Trying wide pHash search (≤{HAMMING_WIDE})...                 ║")
        wide_matches = db_module.find_by_hamming(phash_int, max_distance=HAMMING_WIDE)

        if wide_matches:
            best         = wide_matches[0]
            hamming_dist = best.get("hamming_dist", 0)
            record       = best
            print(f"║   Result:     WIDE MATCH FOUND (Dist: {hamming_dist})              ║")
            print(f"║   Match ID:   {record.get('id')[:28]:<28} ║")

            status     = "VERIFIED_MODIFIED"
            confidence = max(0.45, 0.65 - hamming_dist * 0.01)
            _write_audit_log(record.get("id"), g.api_key["id"], status, confidence, hamming_dist, request_id)
            print(f"╚══════════════ VERIFIED (WIDE PHASH) ═══════════════════╝\n")
            return _build_verify_response(
                status, confidence, record, watermark_extracted, c2pa_valid, hamming_dist, request_id,
                match_type="wide_phash"
            )
        else:
            print(f"║   Result:     NO MATCH (Wide ≤{HAMMING_WIDE})                    ║")

        # ── No match ─────────────────────────────────────────────────────────────
        print(f"║ Final Verdict: {status:<40}║")
        print(f"╚════════════════════ UNREGISTERED ══════════════════════╝\n")
        confidence = 1.0
        _write_audit_log(None, g.api_key["id"], status, confidence, None, request_id)
        return _build_verify_response(
            status, confidence, None, False, False, None, request_id
        )
    except Exception:
        err = traceback.format_exc()
        print(f"║ [FATAL] {err.splitlines()[-1][:40]:<40} ║")
        print(f"╚════════════════════════ ERROR ═════════════════════════╝")
        logger.error("Fatal error in verify_image:\n%s", err)
        return internal_error("A fatal server error occurred.")


# ---------------------------------------------------------------------------
# T-052: GET /v1/manifests/<id>
# ---------------------------------------------------------------------------

@api_bp.route("/manifests/<manifest_id>", methods=["GET"])
@require_api_key(kind="verify")
def get_manifest(manifest_id: str):
    """Retrieve a C2PA manifest by ID."""
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM manifests WHERE id = ?", (manifest_id,)
        ).fetchone()
    if not row:
        return not_found(f"Manifest {manifest_id} not found")
    rec = dict(row)
    return jsonify(rec), 200, {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# T-053: GET /v1/records/<id>
# ---------------------------------------------------------------------------

@api_bp.route("/records/<record_id>", methods=["GET"])
@require_api_key(kind="verify")
def get_record(record_id: str):
    """Retrieve a provenance record by ID (redacts session_token bits)."""
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM provenance_records WHERE id = ?", (record_id,)
        ).fetchone()
    if not row:
        return not_found(f"Record {record_id} not found")
    rec = dict(row)
    # Redact embedded session bits by truncating payload_hex display
    if "payload_hex" in rec:
        rec["payload_hex"] = rec["payload_hex"][:16] + "..."  # show only first 8 bytes
    return jsonify(rec), 200


# ---------------------------------------------------------------------------
# T-054: GET /v1/usage
# ---------------------------------------------------------------------------

@api_bp.route("/usage", methods=["GET"])
@require_api_key(kind="verify")
def get_usage():
    """Return API usage statistics for the current key."""
    from provena_flask.auth import _get_usage, _today
    key_id = g.api_key["id"]
    return jsonify({
        "key_id":   key_id,
        "date":     _today(),
        "registers_today": _get_usage(key_id, "register"),
        "verifies_today":  _get_usage(key_id, "verify"),
        "register_limit":  g.api_key.get("daily_register_limit", 100),
        "verify_limit":    g.api_key.get("daily_verify_limit", 500),
    }), 200


# ---------------------------------------------------------------------------
# Legacy endpoints (keep existing demo working) ─ proxies to new paths
# ---------------------------------------------------------------------------

@api_bp.route("/images/register", methods=["POST"])
def legacy_register():
    """
    Legacy POST /api/v1/images/register — maps demo UI to the new neural/C2PA pipeline.
    """
    import cv2, numpy as np
    try:
        if not request.json:
            return jsonify({"error": "Request must be JSON"}), 400
        data = request.json
        for field in ["image", "model_id", "timestamp"]:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        try:
            image_bytes = base64.b64decode(data["image"])
            img = _open_image_safely(image_bytes)
        except Exception as exc:
            return jsonify({"error": f"Invalid image data: {exc}"}), 400

        # --- New Pipeline ---
        record_id = str(uuid.uuid4())
        phash_int, phash_hex = _compute_phash(img)
        
        # Get a 'default' API key for the demo if none exists
        with db_module.get_sqlite_connection() as conn:
            row = conn.execute("SELECT id FROM api_keys LIMIT 1").fetchone()
            if not row:
                # Create a demo key if it's missing (shouldn't happen with run_migrations)
                key_id = str(uuid.uuid4())
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO api_keys (id, key_hash, org_name, created_at) VALUES (?, ?, ?, ?)",
                    (key_id, "demo_hash", "Demo Org", now_str)
                )
            else:
                key_id = row[0]

        from provena_flask.services import payload_codec
        record_id_int = int(record_id.replace('-', ''), 16) % (2**32)
        payload_bytes = payload_codec.encode(record_id_int)
        payload_hex = payload_bytes.hex()
        
        # 1. Neural Watermarking
        watermarked_img = neural_watermark_service.embed(img, payload_bytes)
        
        # 2. C2PA Embedding
        # Convert PIL back to bytes for C2PA
        out_buf = io.BytesIO()
        watermarked_img.save(out_buf, format="PNG")
        watermarked_bytes = out_buf.getvalue()
        
        # Parse timestamp string to datetime object
        try:
            ts_dt = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts_dt = datetime.now(timezone.utc)

        manifest_id, final_bytes = c2pa_service.create_manifest(
            image_bytes=watermarked_bytes,
            model_id=data["model_id"],
            creator_did="did:provena:demo",
            timestamp=ts_dt
        )
        
        # 3. Database Insertion
        record = {
            "id": record_id,
            "image_phash_int": phash_int,
            "image_phash_hex": phash_hex,
            "model_id": data["model_id"],
            "creator_did": "did:provena:demo",
            "registered_at": data["timestamp"],
            "payload_hex": payload_hex,
            "manifest_id": manifest_id,
            "api_key_id": key_id,
            "custom_fields": "{}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        with db_module.get_connection() as conn:
            _insert_record(conn, record)

        return jsonify({
            "status": "success",
            "image_id": record_id,
            "watermarked_image": base64.b64encode(final_bytes).decode("utf-8"),
            "perceptual_hash": phash_hex,
            "signature": "c2pa_embedded",
            "public_key": "c2pa_ed25519",
            "key_id": key_id,
            "registered_at": record["registered_at"],
        }), 201
    except Exception as exc:
        logger.error("Legacy registration error: %s", exc, exc_info=True)
        return jsonify({"error": "Internal server error", "message": str(exc)}), 500


@api_bp.route("/images/verify", methods=["POST"])
def legacy_verify():
    """Backward-compatible proxy to new verify logic (no auth required for demo)."""
    import cv2, numpy as np
    try:
        if not request.json:
            return jsonify({"error": "Request must be JSON"}), 400
        data = request.json
        if "image" not in data:
            return jsonify({"error": "Missing required field: image"}), 400

        try:
            image_bytes = base64.b64decode(data["image"])
            img = _open_image_safely(image_bytes)
        except Exception as exc:
            return jsonify({"error": f"Invalid image data: {exc}"}), 400

        # --- Decision Tree Path 1: C2PA ---
        manifest_result = c2pa_service.verify_manifest(image_bytes)
        c2pa_valid = manifest_result.valid
        signature_invalid = (manifest_result.error == "SIGNATURE_INVALID")

        record = None
        if c2pa_valid:
            record = _get_record_by_manifest(manifest_result.manifest_id)

        # --- Path 2: Neural Extraction ---
        watermark_extracted = False
        if not record:
            try:
                payload_bytes, neural_conf = neural_watermark_service.extract(img)
                if neural_conf >= NEURAL_CONFIDENCE_THRESHOLD:
                    watermark_extracted = True
                    record = _get_record_by_payload(payload_bytes.hex())
            except Exception:
                pass
        else:
            # If we found it via C2PA, let's still check if watermark is there for UI feedback
            try:
                _, neural_conf = neural_watermark_service.extract(img)
                watermark_extracted = (neural_conf >= NEURAL_CONFIDENCE_THRESHOLD)
            except Exception:
                pass

        # --- Path 3: pHash Fallback ---
        perceptual_match = False
        phash_int, phash_hex = _compute_phash(img)
        if not record:
            matches = db_module.find_by_hamming(phash_int, max_distance=HAMMING_MAX)
            if matches:
                record = matches[0]
                perceptual_match = True
        else:
            # If we already have a record, confirm it matches the image pHash roughly
            rec_phash_int = int(record.get("image_phash_int") or 0)
            dist = bin(phash_int ^ rec_phash_int).count('1')
            perceptual_match = (dist <= HAMMING_MAX)

        if not record:
            return jsonify({
                "status": "not_found",
                "message": "No provenance record found",
                "verification": {
                    "watermark_extracted": False,
                    "signature_valid": False,
                    "perceptual_match": False
                }
            }), 404

        # Map to Legacy Statuses
        if signature_invalid:
            status = "tampered"
        elif watermark_extracted and c2pa_valid:
            status = "verified"
        elif c2pa_valid or watermark_extracted:
            status = "verified"
        elif perceptual_match:
            status = "verified_modified"
        else:
            status = "tampered"

        return jsonify({
            "status": status,
            "image_id": record.get("id"),
            "provenance": {
                "model_id": record.get("model_id"),
                "timestamp": record.get("registered_at") or record.get("created_at"),
                "prompt_hash": record.get("prompt_hash"),
                "key_id": record.get("api_key_id"),
                "created_at": record.get("created_at"),
            },
            "verification": {
                "watermark_extracted": watermark_extracted,
                "signature_valid": c2pa_valid,
                "perceptual_match": perceptual_match,
            },
        }), 200
    except Exception as exc:
        logger.error("Legacy verify error: %s", exc, exc_info=True)
        return jsonify({"error": "Internal server error", "message": str(exc)}), 500


# Keep existing provenance and report endpoints intact
@api_bp.route("/provenance/<image_id>", methods=["GET"])
def get_provenance_legacy(image_id):
    """Legacy: retrieve provenance record by image_id."""
    from provena_flask.services.registry_service import RegistryService
    registry = RegistryService()
    provenance = registry.get_provenance(image_id)
    if not provenance:
        return jsonify({"error": "Not found", "message": f"No record for {image_id}"}), 404
    response = {
        "image_id":       provenance["image_id"],
        "model_id":       provenance["model_id"],
        "timestamp":      provenance["timestamp"],
        "prompt_hash":    provenance.get("prompt_hash"),
        "perceptual_hash": provenance["perceptual_hash"],
        "signature":      provenance["signature"].hex(),
        "public_key":     provenance["public_key"].decode("utf-8"),
        "key_id":         provenance["key_id"],
        "created_at":     provenance["created_at"],
    }
    return jsonify(response), 200


@api_bp.route("/report/<image_id>", methods=["GET"])
def get_forensic_report(image_id):
    """Legacy: generate forensic report."""
    try:
        from provena_flask.services.forensic_service import ForensicReportService
        report_format   = request.args.get("format", "json").lower()
        forensic_service = ForensicReportService()
        report = forensic_service.generate_report(image_id)
        if report.get("verdict") == "not_found":
            return jsonify({"error": "Not found", "message": f"No record for {image_id}", "image_id": image_id}), 404
        if report_format == "text":
            text_report = forensic_service.generate_human_readable_report(report)
            return text_report, 200, {"Content-Type": "text/plain; charset=utf-8"}
        return jsonify(report), 200
    except Exception as exc:
        logger.error("Forensic report error for %s: %s", image_id, exc, exc_info=True)
        return jsonify({"error": "Internal server error", "message": str(exc), "image_id": image_id}), 500


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_image_from_request() -> tuple[Optional[bytes], Optional[str]]:
    """
    Parse raw image bytes from JSON (base64) or multipart form-data.
    Returns (image_bytes, error_message). If error_message is not None, abort.
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
        b64  = data.get("image", "")
        if not b64:
            return None, "Missing 'image' field"
        try:
            img_bytes = base64.b64decode(b64)
        except Exception:
            return None, "Invalid base64 in 'image' field"
    elif request.files and "image" in request.files:
        img_bytes = request.files["image"].read()
    else:
        return None, "Provide image as base64 JSON or multipart/form-data 'image' file"

    if len(img_bytes) > MAX_IMAGE_BYTES:
        return None, f"Image exceeds maximum size of {MAX_IMAGE_BYTES // 1024 // 1024} MB"
    return img_bytes, None


def _open_image_safely(image_bytes: bytes) -> Image.Image:
    """
    Open a PIL image with safety checks (T-common-mistake-6).
    Raises ValueError on invalid format or size too small.
    """
    from PIL import Image as _Image, UnidentifiedImageError
    try:
        img = _Image.open(io.BytesIO(image_bytes))
    except UnidentifiedImageError:
        raise ValueError("Unrecognised image format; must be PNG, JPEG, or WebP")

    if img.format not in ("PNG", "JPEG", "WEBP", None):
        raise ValueError(f"Unsupported image format: {img.format}")

    w, h = img.size
    if w < MIN_DIM or h < MIN_DIM:
        raise ValueError(f"Image too small (min {MIN_DIM}×{MIN_DIM}px); got {w}×{h}")

    return img.convert("RGB")


def _compute_phash(img: Image.Image) -> tuple[int, str]:
    """Compute 64-bit perceptual hash. Returns (int, hex_str)."""
    phash_obj = imagehash.phash(img, hash_size=8)
    phash_int = int(str(phash_obj), 16)
    phash_hex = str(phash_obj)
    return phash_int, phash_hex


_model_index_cache: dict[str, int] = {}
_next_model_index = 0


def _model_id_to_index(model_id: str) -> int:
    """Map model_id string to a stable 0-255 integer index."""
    global _next_model_index
    if model_id not in _model_index_cache:
        _model_index_cache[model_id] = _next_model_index % 256
        _next_model_index += 1
    return _model_index_cache[model_id]


def _make_session_token(api_key_id: str) -> bytes:
    """Generate 8-byte session token from API key ID."""
    import hashlib
    digest = hashlib.sha256(api_key_id.encode()).digest()
    return digest[:8]


def _insert_record(conn, record: dict) -> None:
    """Insert a provenance record (SQLite or PostgreSQL compatible)."""
    # Use string for large ints just in case
    phash_int_str = str(record["image_phash_int"])
    
    if db_module.USE_POSTGRES:
        # PostgreSQL syntax
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO provenance_records
                  (id, image_phash, image_phash_int, image_phash_hex, model_id, creator_did,
                   registered_at, payload_hex, manifest_id, api_key_id, custom_fields, created_at)
                VALUES (%s, %s::bit(64), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_id, image_phash_hex, registered_at) DO NOTHING
                """,
                (
                    record["id"], format(record["image_phash_int"], "064b"), record["image_phash_int"],
                    record["image_phash_hex"], record["model_id"], record["creator_did"],
                    record["registered_at"], record["payload_hex"], record["manifest_id"],
                    record["api_key_id"], record["custom_fields"], record["created_at"],
                ),
            )
    else:
        # SQLite syntax
        phash_int_str = str(record["image_phash_int"])
        conn.execute(
            """
            INSERT OR IGNORE INTO provenance_records
              (id, image_phash_int, image_phash_hex, model_id, creator_did,
               registered_at, payload_hex, manifest_id, api_key_id, custom_fields, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                phash_int_str,
                record["image_phash_hex"],
                record["model_id"],
                record["creator_did"],
                record["registered_at"],
                record["payload_hex"],
                record["manifest_id"],
                record["api_key_id"],
                record["custom_fields"],
                record["created_at"],
            ),
        )


def _get_record_by_manifest(manifest_id: Optional[str]) -> Optional[dict]:
    """Fetch a provenance record by manifest_id."""
    if not manifest_id:
        return None
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM provenance_records WHERE manifest_id = ?",
            (manifest_id,),
        ).fetchone()
    return dict(row) if row else None


def _get_record_by_payload(payload_hex: str) -> Optional[dict]:
    """Fetch a provenance record by exact payload hex."""
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM provenance_records WHERE payload_hex = ?",
            (payload_hex,),
        ).fetchone()
    return dict(row) if row else None


def _build_verify_response(
    status: str,
    confidence: float,
    record: Optional[dict],
    watermark_extracted: bool,
    c2pa_valid: bool,
    hamming_dist: Optional[int],
    request_id: str,
    match_type: Optional[str] = None,
):
    """Build the standardised verify JSON response."""
    body = {
        "status":             status,
        "confidence":         round(confidence, 4),
        "watermark_extracted": watermark_extracted,
        "c2pa_valid":         c2pa_valid,
        "hamming_distance":   hamming_dist,
        "request_id":         request_id,
    }
    if match_type:
        body["match_type"] = match_type
    if record:
        body["record_id"]      = record.get("id")
        body["registered_at"]  = record.get("registered_at") or record.get("created_at")
        body["model_id"]       = record.get("model_id")
        body["creator_did"]    = record.get("creator_did")
        body["manifest_id"]    = record.get("manifest_id")

    return jsonify(body), 200, {"X-Request-ID": request_id}


def _write_audit_log(
    record_id: Optional[str],
    api_key_id: str,
    status: str,
    confidence: float,
    hamming_dist: Optional[int],
    request_id: str,
) -> None:
    """T-051: Append a row to the verification audit log (never update/delete)."""
    try:
        log_id = str(uuid.uuid4())
        now    = datetime.now(timezone.utc).isoformat()
        with db_module.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO verification_log
                  (id, record_id, api_key_id, queried_at, hamming_distance,
                   status_returned, confidence, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (log_id, record_id, api_key_id, now, hamming_dist, status, confidence, request_id),
            )
    except Exception as exc:
        logger.warning("Audit log write failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Idempotency cache (in-memory; use Redis in production)
# ---------------------------------------------------------------------------
_idempotency_cache: dict[str, dict] = {}


def _idempotency_get(key: str) -> Optional[dict]:
    return _idempotency_cache.get(key)


def _idempotency_set(key: str, value: dict) -> None:
    # Limit cache size to 1000 entries
    if len(_idempotency_cache) > 1000:
        oldest = next(iter(_idempotency_cache))
        del _idempotency_cache[oldest]
    _idempotency_cache[key] = value
