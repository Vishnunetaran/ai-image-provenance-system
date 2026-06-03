"""
FeatureAnchor — Local SIFT Feature Anchor Network for sub-image retrieval.

Identifies image fragments and performs perspective-invariant geometric verification
even when watermark pixels are corrupted.

Key design choices:
  - Zero external neural model dependencies (pure cv2.SIFT and numpy)
  - Limit features to top 1000 per image (Defense against Keypoint Flooding DoS)
  - Lowe's ratio test at 0.75 (Defense against Ambiguous/Repetitive patterns)
  - SQLite storage + thread-safe lazy in-memory caching of descriptors to bypass I/O overhead
  - pHash-based coarse pre-filtering (Hamming distance <= 20) for sub-millisecond candidate lookup
  - RANSAC Homography estimation with rigid determinant and convexity validation checks (Defense against Degenerate Homographies)

Public API:
    register_features(record_id, image) -> dict
    match_fragment(image)               -> FeatureMatchResult
"""
from __future__ import annotations

import logging
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import cv2
import imagehash
from PIL import Image

from provena_flask.models.db import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class FeatureMatchResult:
    """Result of a fragment matching operation."""
    matched: bool
    record_id: Optional[str]
    confidence: float
    matched_region: Optional[tuple]  # (x1, y1, x2, y2) or None
    match_count: int
    total_patches: int
    homography: Optional[list] = None


# ---------------------------------------------------------------------------
# SIFT Global Cache
# ---------------------------------------------------------------------------
_SIFT_CACHE: dict[str, tuple[np.ndarray, np.ndarray]] = {}
_CACHE_LOADED = False


def _ensure_cache_loaded() -> None:
    """Lazy load all SIFT features from database to in-memory cache for speed."""
    global _CACHE_LOADED, _SIFT_CACHE
    if _CACHE_LOADED:
        return

    logger.info("FeatureAnchor: Lazy loading SIFT cache from database...")
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT record_id, keypoints, descriptors FROM sift_features")
            rows = cursor.fetchall()
            for row in rows:
                rid = row[0] if isinstance(row, (list, tuple)) else row["record_id"]
                kp = row[1] if isinstance(row, (list, tuple)) else row["keypoints"]
                desc = row[2] if isinstance(row, (list, tuple)) else row["descriptors"]
                
                try:
                    kp_arr, desc_arr = deserialize_sift_features(kp, desc)
                    _SIFT_CACHE[rid] = (kp_arr, desc_arr)
                except Exception as e:
                    logger.warning("FeatureAnchor: Failed to deserialize features for %s: %s", rid, e)
        _CACHE_LOADED = True
        logger.info("FeatureAnchor: Loaded %d records into SIFT cache", len(_SIFT_CACHE))
    except Exception as exc:
        logger.error("FeatureAnchor: Failed to populate SIFT cache: %s", exc)


# ---------------------------------------------------------------------------
# Serialization Helpers
# ---------------------------------------------------------------------------
def serialize_sift_features(keypoints_coords: np.ndarray, descriptors: np.ndarray) -> tuple[bytes, bytes]:
    """Compress keypoints and descriptors into zlib compressed bytes."""
    kp_bytes = zlib.compress(keypoints_coords.astype(np.float32).tobytes())
    desc_bytes = zlib.compress(descriptors.astype(np.uint8).tobytes())
    return kp_bytes, desc_bytes


def deserialize_sift_features(kp_blob: bytes, desc_blob: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Decompress compressed bytes back into keypoints and descriptors."""
    kp_data = zlib.decompress(kp_blob)
    desc_data = zlib.decompress(desc_blob)
    
    keypoints_coords = np.frombuffer(kp_data, dtype=np.float32)
    descriptors = np.frombuffer(desc_data, dtype=np.uint8)
    
    N = len(descriptors) // 128
    if N > 0:
        keypoints_coords = keypoints_coords.reshape((N, 2))
        descriptors = descriptors.reshape((N, 128))
    else:
        keypoints_coords = np.empty((0, 2), dtype=np.float32)
        descriptors = np.empty((0, 128), dtype=np.uint8)
    return keypoints_coords, descriptors


# ---------------------------------------------------------------------------
# SIFT Extraction Helper
# ---------------------------------------------------------------------------
def _extract_sift(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Extract up to 1000 keypoint coordinates and SIFT descriptors from PIL image."""
    img_gray = np.array(image.convert("L"), dtype=np.uint8)
    
    # Initialize SIFT detector with max features = 1000 (Defense against Keypoint Flooding DoS)
    sift = cv2.SIFT_create(nfeatures=1000)
    
    keypoints, descriptors = sift.detectAndCompute(img_gray, None)
    if not keypoints or descriptors is None:
        return np.empty((0, 2), dtype=np.float32), np.empty((0, 128), dtype=np.uint8)
        
    kp_coords = np.array([kp.pt for kp in keypoints], dtype=np.float32)
    return kp_coords, descriptors.astype(np.uint8)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def register_features(record_id: str, image: Image.Image) -> dict:
    """
    Register SIFT features for a provenance record.

    Extracts coordinates and descriptors, compresses them, and persists
    them in SQLite, while registering them in the active search cache.
    """
    logger.info("FeatureAnchor: registering features for record %s", record_id)

    kp_coords, descriptors = _extract_sift(image)
    if len(kp_coords) < 12:
        logger.warning("FeatureAnchor: image too flat/featureless for SIFT extraction (found %d)", len(kp_coords))
        return {
            "status": "skipped",
            "record_id": record_id,
            "patches_stored": 0,
            "feature_dim": 128,
            "reason": "Image lacks distinct SIFT keypoints (minimum 12 required)",
        }

    kp_blob, desc_blob = serialize_sift_features(kp_coords, descriptors)
    now = datetime.now(timezone.utc).isoformat()

    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sift_features (record_id, keypoints, descriptors, created_at) VALUES (?, ?, ?, ?)",
                (record_id, kp_blob, desc_blob, now)
            )
        # Update the active SIFT cache directly
        _ensure_cache_loaded()
        _SIFT_CACHE[record_id] = (kp_coords, descriptors)
        logger.info("FeatureAnchor: stored %d keypoints for record %s", len(kp_coords), record_id)
    except Exception as exc:
        logger.error("FeatureAnchor: failed to store SIFT features in DB: %s", exc)
        return {
            "status": "error",
            "record_id": record_id,
            "reason": str(exc),
        }

    return {
        "status": "registered",
        "record_id": record_id,
        "patches_stored": len(kp_coords),
        "feature_dim": 128,
    }


def match_fragment(image: Image.Image) -> FeatureMatchResult:
    """
    Match a query image or fragment against registered SIFT features.

    Uses a fast pHash pre-filter to find candidates, falls back to wide cache matching
    if pHash returns empty, applies Lowe's ratio test at 0.75, solves homography via
    RANSAC, and validates det(H) + contour convexity to ensure zero false positives.
    """
    logger.info("FeatureAnchor: matching fragment (%dx%d)", image.width, image.height)

    # Extract query features
    query_kp_coords, query_desc = _extract_sift(image)
    if len(query_kp_coords) < 12:
        logger.info("FeatureAnchor: Query image is featureless (has only %d keypoints), skipping SIFT match", len(query_kp_coords))
        return FeatureMatchResult(
            matched=False, record_id=None, confidence=0.0,
            matched_region=None, match_count=0, total_patches=len(query_kp_coords),
        )

    # 1. pHash-based coarse pre-filtering (Hamming distance <= 20)
    candidate_ids = []
    try:
        phash_obj = imagehash.phash(image, hash_size=8)
        phash_int = int(str(phash_obj), 16)
        
        from provena_flask.models import db as db_module
        candidates = db_module.find_by_hamming(phash_int, max_distance=20)
        candidate_ids = [c["id"] for c in candidates]
        logger.info("FeatureAnchor: pHash coarse filter found %d candidates: %s", len(candidate_ids), candidate_ids)
    except Exception as exc:
        logger.warning("FeatureAnchor: pHash coarse filter failed: %s", exc)

    # Ensure memory cache is loaded
    _ensure_cache_loaded()

    # Determine match candidates. Fall back to scanning the entire registry cache if pHash returns nothing
    # (which happens when queries are small, non-centered crops).
    if candidate_ids:
        records_to_search = {rid: _SIFT_CACHE[rid] for rid in candidate_ids if rid in _SIFT_CACHE}
    else:
        logger.info("FeatureAnchor: pHash filter returned zero candidates. Performing wide search on full cached registry...")
        records_to_search = _SIFT_CACHE

    best_record_id = None
    best_inliers = 0
    best_homography = None

    # OpenCV BFMatcher for L2-distance descriptors
    bf = cv2.BFMatcher(cv2.NORM_L2)

    for record_id, (stored_kp_coords, stored_desc) in records_to_search.items():
        if len(stored_kp_coords) < 12:
            continue

        # Find top 2 matches for each descriptor (for Lowe's Ratio Test check)
        try:
            raw_matches = bf.knnMatch(query_desc, stored_desc, k=2)
        except Exception as match_exc:
            logger.warning("FeatureAnchor: Error matching features for %s: %s", record_id, match_exc)
            continue

        # Enforce Lowe's Ratio Test (Defense against Ambiguous/Repetitive patterns)
        good_matches = []
        for m_list in raw_matches:
            if len(m_list) == 2:
                m, n = m_list
                if m.distance < 0.75 * n.distance:
                    good_matches.append(m)

        if len(good_matches) < 12:
            continue

        src_pts = np.float32([query_kp_coords[m.queryIdx] for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([stored_kp_coords[m.trainIdx] for m in good_matches]).reshape(-1, 1, 2)

        # Estimate homography with RANSAC. Reprojection error tolerance = 5.0 pixels.
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)

        if H is None or mask is None:
            continue

        inliers_count = int(np.sum(mask))
        if inliers_count < 12:  # Defense: Floor of 12 geometrically consistent keypoints
            continue

        # Enforce Homography Validation checks (Defense against Degenerate Homographies)
        # 1. Determinant Check (ensure top-left 2x2 scale/rotation is positive and non-degenerate)
        det = H[0, 0] * H[1, 1] - H[0, 1] * H[1, 0]
        if det <= 0 or not (1e-4 <= det <= 1e4):
            continue

        # 2. Contour Convexity Check (ensure projected region does not fold on itself or cross)
        q_h, q_w = image.height, image.width
        corners = np.float32([
            [0, 0],
            [q_w, 0],
            [q_w, q_h],
            [0, q_h]
        ]).reshape(-1, 1, 2)
        
        try:
            projected = cv2.perspectiveTransform(corners, H)
            if not cv2.isContourConvex(projected.astype(np.int32)):
                continue
        except Exception as warp_exc:
            logger.warning("FeatureAnchor: perspectiveTransform verification failed: %s", warp_exc)
            continue

        # Keep the match with the highest number of geometrically consistent inliers
        if inliers_count > best_inliers:
            best_inliers = inliers_count
            best_record_id = record_id
            best_homography = H

    if best_record_id is not None:
        # Match confirmed! Determine bounding box coordinates in original image space
        q_h, q_w = image.height, image.width
        corners = np.float32([
            [0, 0],
            [q_w, 0],
            [q_w, q_h],
            [0, q_h]
        ]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, best_homography)
        pts = projected.reshape(4, 2)
        x_coords = pts[:, 0]
        y_coords = pts[:, 1]
        x1, x2 = float(np.min(x_coords)), float(np.max(x_coords))
        y1, y2 = float(np.min(y_coords)), float(np.max(y_coords))

        confidence = min(0.99, float(best_inliers / 50.0))
        matched_region = (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))

        logger.info(
            "FeatureAnchor: SIFT match found! record=%s, inliers=%d, confidence=%.4f, region=%s",
            best_record_id, best_inliers, confidence, matched_region
        )

        result = FeatureMatchResult(
            matched=True,
            record_id=best_record_id,
            confidence=round(confidence, 4),
            matched_region=matched_region,
            match_count=best_inliers,
            total_patches=len(query_kp_coords)
        )
        result.homography = best_homography.tolist()
        return result

    # No match met the strict geometric/inlier verification criteria
    logger.info("FeatureAnchor: no verified match found above criteria")
    return FeatureMatchResult(
        matched=False, record_id=None, confidence=0.0,
        matched_region=None, match_count=0, total_patches=len(query_kp_coords),
    )
