"""
AdaptiveParams — Adversarial Self-Hardening parameter engine.

Tracks verification outcomes (which watermark layers survived which attacks)
in a rolling window and automatically tunes watermark embedding parameters
to harden against observed attack patterns.

Key design:
  - Rolling window of last 1000 verification outcomes in SQLite
  - Exponential moving average for parameter adaptation
  - Attack classification heuristic based on layer survival patterns
  - Persistent parameter storage in key-value SQLite table

Public API:
    record_outcome(layers_survived, attack_type) -> None
    get_current_params()                          -> AdaptiveParams
    classify_attack(layers_survived)              -> str
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from provena_flask.models.db import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ROLLING_WINDOW_SIZE = 1000
EMA_ALPHA = 0.1  # exponential moving average smoothing factor

# Parameter bounds
QUANT_STEP_MIN = 10
QUANT_STEP_MAX = 50
QUANT_STEP_DEFAULT = 25

TILE_OVERLAP_MIN = 0.25
TILE_OVERLAP_MAX = 0.85
TILE_OVERLAP_DEFAULT = 0.5

SPATIAL_REPS_MIN = 3
SPATIAL_REPS_MAX = 15
SPATIAL_REPS_DEFAULT = 7

FEATURE_DENSITY_MIN = 0.5
FEATURE_DENSITY_MAX = 2.0
FEATURE_DENSITY_DEFAULT = 1.0


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass
class AdaptiveParams:
    """Current adaptive watermark parameters."""
    quant_step: int = QUANT_STEP_DEFAULT
    tile_overlap: float = TILE_OVERLAP_DEFAULT
    spatial_reps: int = SPATIAL_REPS_DEFAULT
    feature_density: float = FEATURE_DENSITY_DEFAULT


# ---------------------------------------------------------------------------
# Attack classification
# ---------------------------------------------------------------------------

def classify_attack(layers_survived: dict[str, bool]) -> str:
    """
    Heuristic attack classification based on layer survival patterns.

    Determines the likely attack type from which layers survived verification:
      - Only neural survived → 'compression' (JPEG, WebP, etc.)
      - Only spatial failed  → 'noise' (additive noise, blur)
      - All layers failed    → 'regeneration' (AI re-generation)
      - Tiled DCT failed but others survived → 'crop'
      - Otherwise            → 'unknown'

    Args:
        layers_survived: Dict mapping layer name to survival boolean.
                         Expected keys: 'neural', 'dct', 'spatial'
                         (prefix matching is used for flexibility).

    Returns:
        Attack type string: 'compression', 'noise', 'regeneration',
        'crop', or 'unknown'.
    """
    # Normalise keys for flexible matching
    neural = _get_layer_status(layers_survived, "neural")
    dct = _get_layer_status(layers_survived, "dct")
    spatial = _get_layer_status(layers_survived, "spatial")

    all_survived = [neural, dct, spatial]
    all_failed = all(not s for s in all_survived)
    all_passed = all(all_survived)

    if all_failed:
        return "regeneration"

    if all_passed:
        return "none"

    # Only neural survived
    if neural and not dct and not spatial:
        return "compression"

    # Only spatial failed (neural + DCT survived)
    if neural and dct and not spatial:
        return "noise"

    # DCT failed but others survived
    if not dct and (neural or spatial):
        return "crop"

    return "unknown"


def _get_layer_status(layers: dict[str, bool], prefix: str) -> bool:
    """Get survival status for a layer by prefix matching."""
    for key, value in layers.items():
        if key.lower().startswith(prefix):
            return bool(value)
    return False


# ---------------------------------------------------------------------------
# Persistent config helpers
# ---------------------------------------------------------------------------

def _load_config(conn) -> dict[str, str]:
    """Load all adaptive_config key-value pairs."""
    try:
        cursor = conn.execute("SELECT key, value FROM adaptive_config")
        return {row[0] if isinstance(row, (list, tuple)) else row["key"]:
                row[1] if isinstance(row, (list, tuple)) else row["value"]
                for row in cursor.fetchall()}
    except Exception:
        return {}


def _save_config(conn, key: str, value: str) -> None:
    """Upsert a single key-value pair in adaptive_config."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO adaptive_config (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
           updated_at = excluded.updated_at""",
        (key, value, now),
    )


def _save_params(conn, params: AdaptiveParams) -> None:
    """Persist all adaptive parameters to the config table."""
    for key, value in asdict(params).items():
        _save_config(conn, f"param_{key}", str(value))


def _load_params(conn) -> AdaptiveParams:
    """Load adaptive parameters from config table, with defaults."""
    config = _load_config(conn)
    return AdaptiveParams(
        quant_step=int(config.get("param_quant_step", str(QUANT_STEP_DEFAULT))),
        tile_overlap=float(config.get("param_tile_overlap", str(TILE_OVERLAP_DEFAULT))),
        spatial_reps=int(config.get("param_spatial_reps", str(SPATIAL_REPS_DEFAULT))),
        feature_density=float(config.get("param_feature_density", str(FEATURE_DENSITY_DEFAULT))),
    )


# ---------------------------------------------------------------------------
# Rolling window management
# ---------------------------------------------------------------------------

def _trim_rolling_window(conn) -> None:
    """Trim verification_telemetry to the most recent ROLLING_WINDOW_SIZE rows."""
    conn.execute(
        """DELETE FROM verification_telemetry
           WHERE id NOT IN (
               SELECT id FROM verification_telemetry
               ORDER BY timestamp DESC LIMIT ?
           )""",
        (ROLLING_WINDOW_SIZE,),
    )


def _compute_failure_rates(conn) -> dict[str, float]:
    """
    Compute per-layer failure rates from the rolling window.

    Returns:
        Dict with keys 'neural', 'dct', 'spatial', 'all_failed',
        each mapping to a failure rate in [0, 1].
    """
    cursor = conn.execute(
        """SELECT layers_json FROM verification_telemetry
           ORDER BY timestamp DESC LIMIT ?""",
        (ROLLING_WINDOW_SIZE,),
    )
    rows = cursor.fetchall()

    if not rows:
        return {"neural": 0.0, "dct": 0.0, "spatial": 0.0, "all_failed": 0.0}

    neural_fails = 0
    dct_fails = 0
    spatial_fails = 0
    all_fails = 0
    total = len(rows)

    for row in rows:
        layers_json = row[0] if isinstance(row, (list, tuple)) else row["layers_json"]
        try:
            layers = json.loads(layers_json)
        except (json.JSONDecodeError, TypeError):
            continue

        neural_ok = _get_layer_status(layers, "neural")
        dct_ok = _get_layer_status(layers, "dct")
        spatial_ok = _get_layer_status(layers, "spatial")

        if not neural_ok:
            neural_fails += 1
        if not dct_ok:
            dct_fails += 1
        if not spatial_ok:
            spatial_fails += 1
        if not neural_ok and not dct_ok and not spatial_ok:
            all_fails += 1

    return {
        "neural": neural_fails / total,
        "dct": dct_fails / total,
        "spatial": spatial_fails / total,
        "all_failed": all_fails / total,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_outcome(
    layers_survived: dict[str, bool],
    attack_type: Optional[str] = None,
) -> None:
    """
    Record a verification outcome in the rolling telemetry window.

    Logs which watermark layers survived the verification attempt and
    the detected (or provided) attack type. Trims the rolling window
    to the most recent 1000 entries and recomputes adaptive parameters.

    Args:
        layers_survived: Dict mapping layer name to survival boolean.
                         e.g. {'neural': True, 'dct': False, 'spatial': True}
        attack_type:     Optional attack type string. If None, it will be
                         auto-classified from the layer survival pattern.
    """
    if attack_type is None:
        attack_type = classify_attack(layers_survived)

    # Determine overall outcome
    any_survived = any(layers_survived.values())
    outcome = "partial" if any_survived else "failed"
    if all(layers_survived.values()):
        outcome = "passed"

    entry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    layers_json = json.dumps(layers_survived)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO verification_telemetry
               (id, timestamp, layers_json, attack_type, outcome)
               VALUES (?, ?, ?, ?, ?)""",
            (entry_id, now, layers_json, attack_type, outcome),
        )

        _trim_rolling_window(conn)

        # Recompute and persist adaptive params
        params = _recompute_params(conn)
        _save_params(conn, params)

    logger.info(
        "AdaptiveParams: recorded outcome=%s, attack=%s, "
        "layers=%s, new_params=%s",
        outcome, attack_type, layers_survived, asdict(params),
    )


def get_current_params() -> AdaptiveParams:
    """
    Get the current adaptive watermark parameters.

    Loads persisted parameters from the database. If no parameters
    have been computed yet, returns defaults.

    Returns:
        AdaptiveParams with current optimal settings.
    """
    with get_connection() as conn:
        return _load_params(conn)


# ---------------------------------------------------------------------------
# Parameter recomputation
# ---------------------------------------------------------------------------

def _recompute_params(conn) -> AdaptiveParams:
    """
    Recompute adaptive parameters from the rolling window using EMA.

    Adaptation rules:
      - DCT failure rate > 40%  → increase quant_step by 5 (max 50)
      - Spatial failure rate > 80% → increase spatial_reps (max 15)
      - ALL layers fail > 30%  → increase tile_overlap to 0.75
      - Low failure rates → gradually decay params back toward defaults

    Args:
        conn: Active database connection.

    Returns:
        Updated AdaptiveParams.
    """
    rates = _compute_failure_rates(conn)
    current = _load_params(conn)

    # --- DCT adaptation ---
    if rates["dct"] > 0.40:
        target_quant = min(current.quant_step + 5, QUANT_STEP_MAX)
    elif rates["dct"] < 0.10:
        # Decay toward default when pressure is low
        target_quant = max(current.quant_step - 2, QUANT_STEP_DEFAULT)
    else:
        target_quant = current.quant_step

    # --- Spatial adaptation ---
    if rates["spatial"] > 0.80:
        target_reps = min(current.spatial_reps + 2, SPATIAL_REPS_MAX)
    elif rates["spatial"] < 0.20:
        target_reps = max(current.spatial_reps - 1, SPATIAL_REPS_DEFAULT)
    else:
        target_reps = current.spatial_reps

    # --- Global failure adaptation ---
    if rates["all_failed"] > 0.30:
        target_overlap = max(current.tile_overlap, 0.75)
        target_density = min(current.feature_density + 0.1, FEATURE_DENSITY_MAX)
    elif rates["all_failed"] < 0.05:
        target_overlap = max(current.tile_overlap - 0.05, TILE_OVERLAP_DEFAULT)
        target_density = max(current.feature_density - 0.05, FEATURE_DENSITY_DEFAULT)
    else:
        target_overlap = current.tile_overlap
        target_density = current.feature_density

    # Apply EMA smoothing
    new_params = AdaptiveParams(
        quant_step=_ema_int(current.quant_step, target_quant),
        tile_overlap=round(_ema_float(current.tile_overlap, target_overlap), 3),
        spatial_reps=_ema_int(current.spatial_reps, target_reps),
        feature_density=round(_ema_float(current.feature_density, target_density), 3),
    )

    # Clamp to bounds
    new_params.quant_step = max(QUANT_STEP_MIN, min(new_params.quant_step, QUANT_STEP_MAX))
    new_params.tile_overlap = max(TILE_OVERLAP_MIN, min(new_params.tile_overlap, TILE_OVERLAP_MAX))
    new_params.spatial_reps = max(SPATIAL_REPS_MIN, min(new_params.spatial_reps, SPATIAL_REPS_MAX))
    new_params.feature_density = max(FEATURE_DENSITY_MIN, min(new_params.feature_density, FEATURE_DENSITY_MAX))

    return new_params


def _ema_int(current: int, target: int) -> int:
    """Apply EMA smoothing and round to integer."""
    return round(current + EMA_ALPHA * (target - current))


def _ema_float(current: float, target: float) -> float:
    """Apply EMA smoothing for floats."""
    return current + EMA_ALPHA * (target - current)
