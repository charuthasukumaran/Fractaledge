"""
Regime Transition Probability Engine
--------------------------------------
Analyzes historical regime sequences to predict transition probabilities.
Uses Markov chain with stress trend and spectral width modifiers.
"""
import json
import numpy as np
from database import get_signals, get_latest_signal


REGIMES = ["GREEN", "AMBER", "RED"]


def compute_transition_matrix(symbol: str = "^NSEI", lookback: int = 500) -> dict:
    """
    Build transition probability matrix from historical signals.

    Returns:
        {
            "current_regime": str,
            "transition_probs": {"GREEN": float, "AMBER": float, "RED": float},
            "confidence": float,
            "trend_direction": str,
            "estimated_bars_until_change": int,
            "transition_matrix": {regime: {regime: float}},
            "regime_durations": {regime: float},
        }
    """
    signals = get_signals(limit=lookback, symbol=symbol)

    if len(signals) < 3:
        return {
            "current_regime": signals[-1]["regime_label"] if signals else "UNKNOWN",
            "transition_probs": {"GREEN": 0.34, "AMBER": 0.33, "RED": 0.33},
            "confidence": 0.0,
            "trend_direction": "unknown",
            "estimated_bars_until_change": 0,
            "transition_matrix": {r: {r2: 1 / 3 for r2 in REGIMES} for r in REGIMES},
            "regime_durations": {r: 0 for r in REGIMES},
            "warning": "Not enough data for reliable predictions.",
        }

    regime_seq = [s["regime_label"] for s in signals if s.get("regime_label") in REGIMES]

    if len(regime_seq) < 3:
        current = regime_seq[-1] if regime_seq else "UNKNOWN"
        return {
            "current_regime": current,
            "transition_probs": {"GREEN": 0.34, "AMBER": 0.33, "RED": 0.33},
            "confidence": 0.0,
            "trend_direction": "unknown",
            "estimated_bars_until_change": 0,
            "transition_matrix": {r: {r2: 1 / 3 for r2 in REGIMES} for r in REGIMES},
            "regime_durations": {r: 0 for r in REGIMES},
        }

    # Build raw transition counts
    counts = {r: {r2: 0 for r2 in REGIMES} for r in REGIMES}
    for i in range(len(regime_seq) - 1):
        a, b = regime_seq[i], regime_seq[i + 1]
        if a in counts and b in counts[a]:
            counts[a][b] += 1

    # Normalize to probabilities (add Laplace smoothing)
    matrix = {}
    for r in REGIMES:
        total = sum(counts[r].values()) + len(REGIMES)  # Laplace
        matrix[r] = {r2: round((counts[r][r2] + 1) / total, 4) for r2 in REGIMES}

    current_regime = regime_seq[-1]

    # Factor in stress trend (last 10 signals)
    recent = signals[-10:]
    scores = [s["ensemble_score"] for s in recent if s.get("ensemble_score") is not None]
    trend_direction = "stable"
    if len(scores) >= 3:
        slope = (scores[-1] - scores[0]) / max(len(scores) - 1, 1)
        if slope > 0.01:
            trend_direction = "rising"
        elif slope < -0.01:
            trend_direction = "falling"

    # Adjust probabilities based on trend
    probs = dict(matrix[current_regime])
    if trend_direction == "rising":
        # Stress rising -> boost RED probability
        probs["RED"] = min(1.0, probs["RED"] * 1.2)
        probs["GREEN"] = max(0.01, probs["GREEN"] * 0.8)
    elif trend_direction == "falling":
        # Stress falling -> boost GREEN probability
        probs["GREEN"] = min(1.0, probs["GREEN"] * 1.2)
        probs["RED"] = max(0.01, probs["RED"] * 0.8)

    # Renormalize
    total = sum(probs.values())
    probs = {r: round(probs[r] / total, 4) for r in REGIMES}

    # Compute average regime durations
    durations = {r: [] for r in REGIMES}
    run_length = 1
    for i in range(1, len(regime_seq)):
        if regime_seq[i] == regime_seq[i - 1]:
            run_length += 1
        else:
            durations[regime_seq[i - 1]].append(run_length)
            run_length = 1
    durations[regime_seq[-1]].append(run_length)

    avg_durations = {}
    for r in REGIMES:
        avg_durations[r] = round(np.mean(durations[r]), 1) if durations[r] else 0

    # Estimate bars until change
    current_run = 1
    for i in range(len(regime_seq) - 2, -1, -1):
        if regime_seq[i] == current_regime:
            current_run += 1
        else:
            break
    avg_dur = avg_durations.get(current_regime, 10)
    bars_until_change = max(0, int(avg_dur - current_run))

    # Confidence based on sample size
    total_transitions = sum(sum(c.values()) for c in counts.values())
    confidence = round(min(1.0, total_transitions / 100), 3)

    return {
        "current_regime": current_regime,
        "transition_probs": probs,
        "confidence": confidence,
        "trend_direction": trend_direction,
        "estimated_bars_until_change": bars_until_change,
        "current_run_length": current_run,
        "transition_matrix": matrix,
        "regime_durations": avg_durations,
        "total_transitions": total_transitions,
    }
