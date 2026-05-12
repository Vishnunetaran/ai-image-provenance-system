"""
Majority Vote — Aggregates extraction results from multiple layers.

Given a list of (layer_name, payload_bytes, confidence) tuples from
the HydraWatermark extraction, determines the winning payload by
weighted majority vote.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VoteResult:
    """Result of majority vote aggregation."""
    payload: bytes                          # Winning payload (empty if no consensus)
    confidence: float                       # Aggregate confidence 0.0–1.0
    winner_votes: int                       # Number of layers that agree
    total_votes: int                        # Total layers that returned non-empty
    breakdown: dict = field(default_factory=dict)  # layer_name -> (payload_hex, confidence)


def vote(results: list[tuple[str, bytes, float]], min_confidence: float = 0.5) -> VoteResult:
    """
    Aggregate extraction results via weighted majority vote.

    Args:
        results: List of (layer_name, payload_bytes, confidence) tuples.
        min_confidence: Minimum per-layer confidence to count as a vote.

    Returns:
        VoteResult with the winning payload and aggregate confidence.
    """
    breakdown = {}
    vote_counts: dict[bytes, float] = defaultdict(float)
    vote_raw_counts: dict[bytes, int] = defaultdict(int)
    total_votes = 0

    for layer_name, payload, confidence in results:
        payload_hex = payload.hex() if payload else "empty"
        breakdown[layer_name] = {
            "payload_hex": payload_hex,
            "confidence": round(confidence, 4),
            "voted": False,
        }

        # Only count non-empty payloads above minimum confidence
        if payload and len(payload) > 0 and confidence >= min_confidence:
            vote_counts[payload] += confidence
            vote_raw_counts[payload] += 1
            total_votes += 1
            breakdown[layer_name]["voted"] = True

    if not vote_counts:
        logger.info("Majority vote: no layers met confidence threshold")
        return VoteResult(
            payload=b"",
            confidence=0.0,
            winner_votes=0,
            total_votes=0,
            breakdown=breakdown,
        )

    # Winner = payload with highest weighted vote count
    winner = max(vote_counts, key=lambda k: vote_counts[k])
    winner_raw = vote_raw_counts[winner]

    # Confidence = average confidence of layers that voted for the winner
    # This ensures a single layer with 1.0 confidence isn't diluted by
    # other layers that extracted completely different (wrong) payloads.
    aggregate_conf = vote_counts[winner] / winner_raw if winner_raw > 0 else 0.0

    # Update breakdown: 'voted' means "agreed with the winning payload"
    # not just "had high enough confidence to participate"
    winner_hex = winner.hex()
    for layer_name, info in breakdown.items():
        info["voted"] = (info["payload_hex"] == winner_hex and info["confidence"] >= min_confidence)

    logger.info(
        "Majority vote: winner=%s, votes=%d/%d, confidence=%.3f",
        winner.hex(), winner_raw, total_votes, aggregate_conf,
    )

    return VoteResult(
        payload=winner,
        confidence=round(aggregate_conf, 4),
        winner_votes=winner_raw,
        total_votes=total_votes,
        breakdown=breakdown,
    )
