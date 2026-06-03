"""
HydraWatermark — Multi-layer watermark orchestrator.

Coordinates 3 independent watermark layers that operate in different
mathematical domains:
  1. Neural (TrustMark) — learned encoder/decoder, robust to compression
  2. DCT (frequency)    — coefficient modulation, robust to spatial attacks
  3. Spatial (LSB+ECC)  — pixel-level embedding, robust to frequency attacks

An attacker must independently defeat ALL THREE layers to strip the watermark.

Public API:
    embed(image, payload)  -> watermarked PIL Image
    extract(image)         -> VoteResult (payload + confidence + breakdown)
"""
from __future__ import annotations

import logging
import time
from PIL import Image

from provena_flask.services.layers import neural_layer, dct_layer, spatial_layer, tiled_dct_layer
from provena_flask.services.majority_vote import vote, VoteResult
from provena_flask.services import tamper_grid
from provena_flask.services import feature_anchor

logger = logging.getLogger(__name__)

# Ordered list of layers — embedding happens in this order
LAYERS = [
    ("neural_trustmark", neural_layer),
    ("dct_frequency", dct_layer),
    ("spatial_lsb", spatial_layer),
    ("tiled_dct", tiled_dct_layer),
]


def embed(image: Image.Image, payload: bytes) -> Image.Image:
    """
    Embed payload into image using all 5 watermark layers in a perfect,
    non-interfering sequence:
      1. Neural (TrustMark) - robust global embedding
      2. Tamper Grid        - Blue channel frequency QIM
      3. DCT Layer          - Green channel frequency QIM
      4. Tiled DCT Layer    - Red channel frequency QIM
      5. Spatial Layer      - final pixel-level LSB adjustments
    """
    if len(payload) != 6:
        raise ValueError(f"HydraWatermark expects 6-byte payload, got {len(payload)}")

    result_img = image.convert("RGB")
    t0 = time.time()
    
    # 0. Register with Feature Anchor Network (reads original pixels)
    try:
        record_id_hex = payload.hex()
        feature_anchor.register_features(record_id_hex, result_img)
        logger.info("HydraWatermark: Feature Anchor registration OK")
    except Exception as exc:
        logger.warning("HydraWatermark: Feature Anchor registration FAILED: %s", exc)

    # 1. Neural (TrustMark) - Must be first so subsequent high-frequency layers don't get shifted by encoder
    try:
        t_start = time.time()
        result_img = neural_layer.embed(result_img, payload)
        logger.info("HydraWatermark: Neural TrustMark embed OK (%.0f ms)", (time.time() - t_start) * 1000)
    except Exception as exc:
        logger.warning("HydraWatermark: Neural TrustMark embed FAILED: %s", exc)

    # Decode record_id_int once for layers that need it
    from provena_flask.services import payload_codec
    try:
        record_id_int = payload_codec.decode(payload)
    except Exception:
        record_id_int = 0

    # 2. Tamper Grid - Blue channel (channel 2)
    try:
        t_start = time.time()
        result_img = tamper_grid.embed(result_img, record_id_int, payload)
        logger.info("HydraWatermark: Tamper Grid embed OK (%.0f ms)", (time.time() - t_start) * 1000)
    except Exception as exc:
        logger.warning("HydraWatermark: Tamper Grid embed FAILED: %s", exc)

    # 3. DCT Layer - Green channel (channel 1)
    try:
        t_start = time.time()
        result_img = dct_layer.embed(result_img, payload)
        logger.info("HydraWatermark: DCT Frequency embed OK (%.0f ms)", (time.time() - t_start) * 1000)
    except Exception as exc:
        logger.warning("HydraWatermark: DCT Frequency embed FAILED: %s", exc)

    # 4. Tiled DCT Layer - Red channel (channel 0)
    try:
        t_start = time.time()
        result_img = tiled_dct_layer.embed(result_img, payload)
        logger.info("HydraWatermark: Tiled DCT embed OK (%.0f ms)", (time.time() - t_start) * 1000)
    except Exception as exc:
        logger.warning("HydraWatermark: Tiled DCT embed FAILED: %s", exc)

    # 5. Spatial Layer (LSB) - Must be last to prevent LSB values from being modified by other layers
    try:
        t_start = time.time()
        result_img = spatial_layer.embed(result_img, payload)
        logger.info("HydraWatermark: Spatial LSB embed OK (%.0f ms)", (time.time() - t_start) * 1000)
    except Exception as exc:
        logger.warning("HydraWatermark: Spatial LSB embed FAILED: %s", exc)

    total_ms = round((time.time() - t0) * 1000)
    logger.info("HydraWatermark embed complete: total %.0f ms", total_ms)

    return result_img


def extract(image: Image.Image, min_confidence: float = 0.5) -> VoteResult:
    """
    Extract payload from image using all layers, then majority-vote.

    Supports dynamic sub-image/screenshot query alignment by running the
    Feature Anchor Network first. If matched, the query image is resized
    and padded back to the original registered coordinate space before
    running frequency-domain and spatial extraction.

    Args:
        image:          PIL Image to extract from.
        min_confidence: Minimum per-layer confidence to count as a vote.

    Returns:
        VoteResult with winning payload, confidence, and per-layer breakdown.
    """
    img_rgb = image.convert("RGB")
    t0 = time.time()

    # ── Step 0: Pre-match Feature Anchors to detect crops / screenshots / scale changes ──
    feature_match = None
    original_size = None

    try:
        feature_match_res = feature_anchor.match_fragment(img_rgb)
        if feature_match_res.matched and feature_match_res.record_id:
            feature_match = {
                "matched": feature_match_res.matched,
                "record_id": feature_match_res.record_id,
                "confidence": feature_match_res.confidence,
                "matched_region": feature_match_res.matched_region,
                "match_count": feature_match_res.match_count,
                "total_patches": feature_match_res.total_patches
            }
            if hasattr(feature_match_res, "homography") and feature_match_res.homography:
                feature_match["homography"] = feature_match_res.homography
            # Retrieve the original registered image dimensions from the database record
            from provena_flask.models import db as db_module
            with db_module.get_connection() as conn:
                row = conn.execute(
                    "SELECT custom_fields FROM provenance_records WHERE id = ? OR payload_hex = ?",
                    (feature_match_res.record_id, feature_match_res.record_id)
                ).fetchone()
                
                # Check custom_fields dict for precise dimensions
                if row and row[0]:
                    try:
                        import json
                        cfields = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        orig_w = cfields.get("orig_width")
                        orig_h = cfields.get("orig_height")
                        if orig_w and orig_h:
                            original_size = (int(orig_w), int(orig_h))
                            feature_match["orig_width"] = int(orig_w)
                            feature_match["orig_height"] = int(orig_h)
                            logger.info("HydraWatermark: Found original size in custom_fields: %dx%d", orig_w, orig_h)
                    except Exception as json_exc:
                        logger.warning("HydraWatermark: Failed to parse custom_fields JSON: %s", json_exc)

                if not original_size:
                    # Fallback to patch-based size estimation from stored anchor indexes
                    row = conn.execute(
                        "SELECT MAX(patch_row), MAX(patch_col) FROM feature_anchors WHERE record_id = ?",
                        (feature_match_res.record_id,)
                    ).fetchone()
                    if row and row[0] is not None and row[1] is not None:
                        max_row, max_col = row[0], row[1]
                        orig_h = max_row * 32 + 64
                        orig_w = max_col * 32 + 64
                        original_size = (orig_w, orig_h)
                        feature_match["orig_width"] = int(orig_w)
                        feature_match["orig_height"] = int(orig_h)
                        logger.info("HydraWatermark: Estimated original size from patch indexes: %dx%d", orig_w, orig_h)
    except Exception as exc:
        logger.warning("HydraWatermark: Pre-extract Feature Anchor match FAILED: %s", exc)

    # ── Step 1: Align query image if it is a scaled/cropped fragment ──
    aligned_img = img_rgb
    aligned = False
    if original_size and feature_match:
        orig_w, orig_h = original_size
        H_list = feature_match.get("homography")
        
        if H_list:
            import cv2
            try:
                H = np.array(H_list, dtype=np.float64)
                query_cv = np.array(img_rgb, dtype=np.uint8)
                
                # Warp query image back to original dimensions space
                warped_cv = cv2.warpPerspective(query_cv, H, (orig_w, orig_h), borderValue=(128, 128, 128))
                aligned_img = Image.fromarray(warped_cv)
                aligned = True
                logger.info(
                    "HydraWatermark: Successfully aligned query image using SIFT homography perspective warp "
                    "to original coordinate space (%dx%d)", orig_w, orig_h
                )
            except Exception as warp_err:
                logger.warning("HydraWatermark: Homography warp alignment failed: %s. Falling back to bounding box.", warp_err)

        if not aligned and feature_match.get("matched_region"):
            x1, y1, x2, y2 = feature_match["matched_region"]
            # Calculate matched region dimensions
            matched_w = x2 - x1
            matched_h = y2 - y1

            if matched_w > 0 and matched_h > 0:
                # Check if query image is already perfectly aligned with registered dimensions
                if (matched_w == orig_w and matched_h == orig_h and
                        x1 == 0 and y1 == 0 and
                        img_rgb.width == orig_w and img_rgb.height == orig_h):
                    logger.info(
                        "HydraWatermark: Query image is already at original registered dimensions (%dx%d) "
                        "with no crop offset. Skipping alignment to prevent resizing/LANCZOS artifacts.",
                        orig_w, orig_h
                    )
                else:
                    # Resize query image back to original matched region dimensions
                    resized_query = img_rgb.resize((matched_w, matched_h), Image.Resampling.LANCZOS)

                    # Create neutral grey canvas of the original dimensions
                    reconstructed = Image.new("RGB", (orig_w, orig_h), (128, 128, 128))
                    reconstructed.paste(resized_query, (x1, y1))
                    aligned_img = reconstructed
                    aligned = True
                    logger.info(
                        "HydraWatermark: Successfully aligned/reconstructed query image via bounding box (%dx%d) "
                        "to original coordinate space (%dx%d)",
                        img_rgb.width, img_rgb.height, orig_w, orig_h
                    )

    # ── Step 2: Extract from the aligned image ──
    extraction_results = []
    for layer_name, layer_module in LAYERS:
        try:
            t_layer = time.time()
            payload_bytes, confidence = layer_module.extract(aligned_img)
            elapsed = time.time() - t_layer
            extraction_results.append((layer_name, payload_bytes, confidence))
            logger.info(
                "HydraWatermark: %s extract -> %s (conf=%.3f, %.0f ms)",
                layer_name,
                payload_bytes.hex() if payload_bytes else "empty",
                confidence,
                elapsed * 1000,
            )
        except Exception as exc:
            extraction_results.append((layer_name, b"", 0.0))
            logger.warning("HydraWatermark: %s extract FAILED: %s", layer_name, exc)

    total_ms = round((time.time() - t0) * 1000)
    result = vote(extraction_results, min_confidence=min_confidence)

    # ── Step 3: Extract Tamper Grid ──
    try:
        tamper_report = tamper_grid.extract(aligned_img)
        # If the image was cropped/aligned, cells outside the cropped region should be marked "unknown"
        # rather than "tampered", because they are outside the query region
        cells_list = []
        for c in tamper_report.cells:
            status = c.status
            # If aligned, check if this cell overlaps with the cropped region.
            # If it's completely outside the cropped region, mark as "unknown"
            if aligned and feature_match and feature_match.get("matched_region"):
                x1, y1, x2, y2 = feature_match["matched_region"]
                cx0, cy0, cx1, cy1 = c.bbox
                # Check for overlap
                overlaps = not (cx1 <= x1 or cx0 >= x2 or cy1 <= y1 or cy0 >= y2)
                if not overlaps:
                    status = "unknown"

            cells_list.append({
                "row": c.row,
                "col": c.col,
                "status": status,
                "confidence": c.confidence,
                "bbox": c.bbox
            })

        # Re-evaluate authentic percentage on valid/evaluated cells only
        evaluated_cells = [c for c in cells_list if c["status"] in ("authentic", "tampered")]
        authentic_cells = [c for c in evaluated_cells if c["status"] == "authentic"]
        if evaluated_cells:
            authentic_percentage = (len(authentic_cells) / len(evaluated_cells)) * 100.0
        else:
            authentic_percentage = 0.0

        # Defensive handling: If the authentic percentage is extremely low (e.g. < 5.0%),
        # it means the tamper grid watermark was either never embedded (legacy/unwatermarked image)
        # or has been completely destroyed (heavy compression). Since we cannot localize tampering
        # in this state, we mark all cells as "unknown" to prevent rendering a misleading full-red grid.
        if authentic_percentage < 5.0:
            for c in cells_list:
                c["status"] = "unknown"
            authentic_percentage = 0.0

        result.tamper_map = {
            "grid_rows": tamper_report.grid_rows,
            "grid_cols": tamper_report.grid_cols,
            "cells": cells_list,
            "authentic_percentage": round(authentic_percentage, 2),
            "tampered_regions": tamper_report.tampered_regions
        }
    except Exception as exc:
        logger.warning("HydraWatermark: Tamper Grid extract FAILED: %s", exc)

    # Attach the matched feature details to result
    if feature_match:
        result.feature_match = feature_match

    logger.info(
        "HydraWatermark extract complete: winner=%s, votes=%d/%d, conf=%.3f (%.0f ms)",
        result.payload.hex() if result.payload else "none",
        result.winner_votes,
        result.total_votes,
        result.confidence,
        total_ms,
    )

    return result
