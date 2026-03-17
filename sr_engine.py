"""
Support & Resistance Engine — detects key price levels from historical candles.
--------------------------------------------------------------------------------
Pure numpy implementation.  No pandas/ta-lib dependencies.
Provides swing-point detection, level clustering, pivot points,
and nearest-support/resistance classification.
"""
import numpy as np
from typing import List, Dict, Optional, Tuple


# ── Swing-Point Detection ──────────────────────────────────────────

def find_swing_points(
    candles: list, lookback: int = 5
) -> Dict[str, List[Tuple[int, float]]]:
    """
    Detect local highs and lows using a ±lookback window.

    A swing high is a bar whose *high* is greater than the highs of the
    `lookback` bars on each side.  Swing lows mirror this with *low* prices.

    Returns {"swing_highs": [(index, price), ...],
             "swing_lows":  [(index, price), ...]}
    """
    n = len(candles)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    if n < 2 * lookback + 1:
        return {"swing_highs": swing_highs, "swing_lows": swing_lows}

    highs = np.array([c["high"] for c in candles], dtype=float)
    lows = np.array([c["low"] for c in candles], dtype=float)

    for i in range(lookback, n - lookback):
        # Check swing high
        window_highs = highs[i - lookback : i + lookback + 1]
        if highs[i] == np.max(window_highs):
            swing_highs.append((i, float(highs[i])))

        # Check swing low
        window_lows = lows[i - lookback : i + lookback + 1]
        if lows[i] == np.min(window_lows):
            swing_lows.append((i, float(lows[i])))

    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


# ── Level Clustering ───────────────────────────────────────────────

def cluster_levels(
    prices: List[float], tolerance: float = 0.003
) -> List[Dict]:
    """
    Group nearby prices (within `tolerance` %) into zones.

    Returns a list of dicts sorted by touch count (descending):
        [{"level": avg_price, "touches": count, "strength": 0-1}, ...]
    """
    if not prices:
        return []

    sorted_prices = sorted(prices)
    clusters: List[List[float]] = []
    current_cluster: List[float] = [sorted_prices[0]]

    for price in sorted_prices[1:]:
        # Compare to the average of the current cluster
        cluster_avg = np.mean(current_cluster)
        if abs(price - cluster_avg) / cluster_avg <= tolerance:
            current_cluster.append(price)
        else:
            clusters.append(current_cluster)
            current_cluster = [price]
    clusters.append(current_cluster)

    # Build result
    max_touches = max(len(c) for c in clusters) if clusters else 1
    result = []
    for cluster in clusters:
        touches = len(cluster)
        result.append({
            "level": round(float(np.mean(cluster)), 2),
            "touches": touches,
            "strength": round(touches / max(max_touches, 1), 3),
        })

    # Sort by touches descending
    result.sort(key=lambda x: x["touches"], reverse=True)
    return result


# ── Classic Pivot Points ───────────────────────────────────────────

def compute_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Classic pivot points from a session's High / Low / Close.

    Returns {"pp", "s1", "s2", "s3", "r1", "r2", "r3"}
    """
    pp = (high + low + close) / 3.0
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)

    return {
        "pp": round(pp, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "r3": round(r3, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "s3": round(s3, 2),
    }


# ── Main Entry Point ──────────────────────────────────────────────

def compute_support_resistance(candles: list) -> dict:
    """
    Main entry point — compute support/resistance levels, pivots, and
    price position within the nearest S/R range.

    Args:
        candles: list of dicts with {open, high, low, close, volume, timestamp}
                 (ideally 200+ bars)

    Returns:
        {
            "support_levels":    [{"level", "touches", "strength"}, ...],
            "resistance_levels": [{"level", "touches", "strength"}, ...],
            "nearest_support":   float,
            "nearest_resistance": float,
            "pivots":            {"pp", "s1", "s2", "s3", "r1", "r2", "r3"},
            "price_position":    float   # 0 = at support, 1 = at resistance
        }
    """
    empty = {
        "support_levels": [],
        "resistance_levels": [],
        "nearest_support": 0,
        "nearest_resistance": 0,
        "pivots": {"pp": 0, "s1": 0, "s2": 0, "s3": 0, "r1": 0, "r2": 0, "r3": 0},
        "price_position": 0.5,
    }

    if not candles or len(candles) < 20:
        return empty

    current_price = float(candles[-1]["close"])

    # 1. Find swing points
    swings = find_swing_points(candles, lookback=5)

    # 2. Cluster swing highs and swing lows
    high_prices = [p for _, p in swings["swing_highs"]]
    low_prices = [p for _, p in swings["swing_lows"]]

    high_levels = cluster_levels(high_prices, tolerance=0.003)
    low_levels = cluster_levels(low_prices, tolerance=0.003)

    # 3. Classify as support or resistance relative to current price
    all_levels = []
    for lvl in high_levels + low_levels:
        all_levels.append(lvl)

    support_levels = []
    resistance_levels = []

    for lvl in all_levels:
        if lvl["level"] < current_price:
            support_levels.append(lvl)
        elif lvl["level"] > current_price:
            resistance_levels.append(lvl)
        else:
            # Exactly at a level — add to both
            support_levels.append(lvl)
            resistance_levels.append(lvl)

    # Sort: support descending (nearest first), resistance ascending (nearest first)
    support_levels.sort(key=lambda x: x["level"], reverse=True)
    resistance_levels.sort(key=lambda x: x["level"])

    # Keep top 5 each
    support_levels = support_levels[:5]
    resistance_levels = resistance_levels[:5]

    # 4. Nearest support & resistance
    nearest_support = support_levels[0]["level"] if support_levels else 0
    nearest_resistance = resistance_levels[0]["level"] if resistance_levels else 0

    # 5. Price position (0 = at support, 1 = at resistance)
    if nearest_support and nearest_resistance and nearest_resistance != nearest_support:
        price_position = (current_price - nearest_support) / (nearest_resistance - nearest_support)
        price_position = round(max(0.0, min(1.0, price_position)), 3)
    else:
        price_position = 0.5

    # 6. Pivot points from the last full session's High/Low/Close
    # Use the last completed candle window (all candles except the last live one)
    session_candles = candles[:-1] if len(candles) > 1 else candles
    session_high = max(c["high"] for c in session_candles)
    session_low = min(c["low"] for c in session_candles)
    session_close = float(session_candles[-1]["close"])
    pivots = compute_pivots(session_high, session_low, session_close)

    return {
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "pivots": pivots,
        "price_position": price_position,
    }


def validate_sr_with_fractal(
    sr_data: dict,
    scale_analysis: dict,
    mfdfa_result: dict,
) -> dict:
    """
    Validate S/R levels against MFDFA dominant scaling regime.

    A level is 'fractal-validated' if it aligns with the dominant timescale
    where the Hurst exponent is strongest (most persistent).

    Args:
        sr_data: output from compute_support_resistance()
        scale_analysis: output from compute_scale_analysis()
        mfdfa_result: raw MFDFA output

    Returns enhanced sr_data with fractal validation scores per level.
    """
    dominant_scale = scale_analysis.get("dominant_scale", 50)
    local_hursts = scale_analysis.get("local_hursts", [])
    hurst = mfdfa_result.get("hurst", 0.5)

    def validate_level(level_dict, level_type):
        """Add fractal validation to a single S/R level."""
        lvl = level_dict.copy()
        touches = lvl.get("touches", 1)
        strength = lvl.get("strength", 0.5)

        # Find closest local Hurst to dominant scale
        closest_h = hurst
        if local_hursts:
            for lh in local_hursts:
                if lh["scale_start"] <= dominant_scale <= lh["scale_end"]:
                    closest_h = lh["hurst"]
                    break

        # Fractal validation score:
        # High if: level has many touches AND Hurst supports persistence
        persistence_bonus = max(0, closest_h - 0.5) * 2  # 0-1 range
        touch_score = min(1.0, touches / 5.0)

        fractal_score = round(0.5 * touch_score + 0.3 * persistence_bonus + 0.2 * strength, 3)
        lvl["fractal_score"] = fractal_score
        lvl["dominant_scale"] = dominant_scale
        lvl["scale_hurst"] = round(closest_h, 4)
        lvl["fractal_validated"] = fractal_score >= 0.4
        return lvl

    validated_support = [validate_level(s, "support") for s in sr_data.get("support_levels", [])]
    validated_resistance = [validate_level(r, "resistance") for r in sr_data.get("resistance_levels", [])]

    result = sr_data.copy()
    result["support_levels"] = validated_support
    result["resistance_levels"] = validated_resistance
    result["dominant_scale"] = dominant_scale
    result["fractal_validated"] = True
    return result
