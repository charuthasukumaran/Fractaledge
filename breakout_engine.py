"""
Breakout Engine — detects when price breaks through support/resistance levels.
------------------------------------------------------------------------------
Combines S/R breach detection with volume and candle-body confirmation.
Returns breakout direction, confidence, and regime alignment.
"""
import numpy as np
from typing import Optional


def detect_breakout(
    candles: list,
    sr_data: dict,
    trend_data: dict,
    regime_label: str = "GREEN",
) -> dict:
    """
    Detect if the latest candle breaks through a support or resistance level.

    Args:
        candles:      list of candle dicts (at least 20)
        sr_data:      output from compute_support_resistance()
        trend_data:   output from compute_trend()
        regime_label: current regime ("GREEN" / "AMBER" / "RED")

    Returns:
        {
            "breakout_detected": bool,
            "direction":         "BULLISH" | "BEARISH" | None,
            "broken_level":      float | None,
            "level_type":        "resistance" | "support" | None,
            "volume_ratio":      float,
            "body_ratio":        float,
            "confidence":        float,    # 0-1
            "regime_alignment":  bool,
            "timestamp":         str | None
        }
    """
    empty = {
        "breakout_detected": False,
        "direction": None,
        "broken_level": None,
        "level_type": None,
        "volume_ratio": 0.0,
        "body_ratio": 0.0,
        "confidence": 0.0,
        "regime_alignment": False,
        "timestamp": None,
    }

    if not candles or len(candles) < 21:
        return empty

    latest = candles[-1]
    prev = candles[-2]
    current_close = float(latest["close"])
    current_open = float(latest["open"])
    prev_close = float(prev["close"])

    # ── Volume & body metrics ──────────────────────────────────────
    lookback = 20
    recent = candles[-lookback - 1 : -1]  # last 20 BEFORE current candle

    avg_volume = np.mean([c.get("volume", 0) for c in recent])
    current_volume = float(latest.get("volume", 0))
    volume_ratio = round(current_volume / avg_volume, 2) if avg_volume > 0 else 0.0

    avg_body = np.mean([abs(c["close"] - c["open"]) for c in recent])
    current_body = abs(current_close - current_open)
    body_ratio = round(current_body / avg_body, 2) if avg_body > 0 else 0.0

    # ── Check resistance breakout (BULLISH) ────────────────────────
    resistance_levels = sr_data.get("resistance_levels", [])
    support_levels = sr_data.get("support_levels", [])

    breakout_detected = False
    direction = None
    broken_level = None
    level_type = None

    # Check resistance breakout: close > resistance AND prev_close <= resistance
    for r in resistance_levels:
        lvl = r["level"]
        if current_close > lvl and prev_close <= lvl:
            breakout_detected = True
            direction = "BULLISH"
            broken_level = lvl
            level_type = "resistance"
            break

    # Check support breakdown: close < support AND prev_close >= support
    if not breakout_detected:
        for s in support_levels:
            lvl = s["level"]
            if current_close < lvl and prev_close >= lvl:
                breakout_detected = True
                direction = "BEARISH"
                broken_level = lvl
                level_type = "support"
                break

    if not breakout_detected:
        empty["volume_ratio"] = volume_ratio
        empty["body_ratio"] = body_ratio
        empty["timestamp"] = latest.get("timestamp")
        return empty

    # ── Confidence calculation ─────────────────────────────────────
    # Volume confirmation: volume > 1.5x average
    vol_confirm = 1.0 if volume_ratio > 1.5 else volume_ratio / 1.5

    # Body confirmation: body > 1.2x average
    body_confirm = 1.0 if body_ratio > 1.2 else body_ratio / 1.2

    # Regime alignment
    # RED + bearish breakout = aligned (stress confirms sell-off)
    # GREEN + bullish breakout = aligned (calm confirms rally)
    regime_alignment = False
    if direction == "BEARISH" and regime_label == "RED":
        regime_alignment = True
    elif direction == "BULLISH" and regime_label == "GREEN":
        regime_alignment = True
    elif direction == "BULLISH" and regime_label == "AMBER":
        regime_alignment = False  # neutral — not strongly aligned
    elif direction == "BEARISH" and regime_label == "AMBER":
        regime_alignment = False

    regime_score = 1.0 if regime_alignment else 0.0

    # Trend alignment
    trend = trend_data.get("trend", "SIDEWAYS")
    trend_aligned = (
        (direction == "BULLISH" and trend == "UPTREND")
        or (direction == "BEARISH" and trend == "DOWNTREND")
    )
    trend_score = 1.0 if trend_aligned else 0.0

    # Composite confidence (weighted)
    confidence = (
        0.30 * vol_confirm
        + 0.25 * body_confirm
        + 0.25 * regime_score
        + 0.20 * trend_score
    )
    confidence = round(min(1.0, confidence), 3)

    return {
        "breakout_detected": True,
        "direction": direction,
        "broken_level": broken_level,
        "level_type": level_type,
        "volume_ratio": volume_ratio,
        "body_ratio": body_ratio,
        "confidence": confidence,
        "regime_alignment": regime_alignment,
        "timestamp": latest.get("timestamp"),
    }


def compute_breakout_quality(
    breakout_data: dict,
    mfdfa_result: dict,
    mfdcca_result: dict = None,
    sr_data: dict = None,
    scale_analysis: dict = None,
) -> dict:
    """
    Fractal-aware breakout quality scoring system.

    Combines:
      1. h(q=2) > 0.5 → trend persistence (breakout-friendly)
      2. Spectrum narrowing → regime stabilizing (cleaner breakout)
      3. MFDCCA alignment with broader market (confirmation)
      4. Scale-validated S/R breach (trigger quality)
      5. Volume & body confirmation (classical)

    Returns enhanced breakout data with quality score and components.
    """
    result = breakout_data.copy()

    if not breakout_data.get("breakout_detected"):
        result["quality_score"] = 0.0
        result["quality_components"] = {}
        result["quality_signal"] = "NO_BREAKOUT"
        return result

    # Component 1: Hurst persistence (h(2) > 0.5 = trend-friendly)
    hurst = mfdfa_result.get("hurst", 0.5)
    if np.isnan(hurst):
        hurst = 0.5
    hurst_score = max(0, min(1, (hurst - 0.35) / 0.3))  # 0.35-0.65 maps to 0-1

    # Component 2: Spectrum width (narrower = more stable = better breakout)
    spectral_width = mfdfa_result.get("spectral_width", 0.5)
    # Invert: narrow spectrum (< 0.2) is good, wide (> 0.7) is bad
    spectrum_score = max(0, min(1, 1.0 - (spectral_width - 0.1) / 0.6))

    # Component 3: MFDCCA market alignment
    mfdcca_score = 0.5  # neutral default
    if mfdcca_result and "error" not in mfdcca_result:
        coupling = mfdcca_result.get("coupling_strength", 0.0)
        # Strong coupling + breakout = sector-driven, more reliable
        rho_q = mfdcca_result.get("rho_q", {})
        rho_2 = rho_q.get(2, 0.0)
        mfdcca_score = max(0, min(1, abs(rho_2)))

    # Component 4: S/R fractal validation quality
    sr_quality = 0.5
    if sr_data and sr_data.get("fractal_validated"):
        direction = breakout_data.get("direction")
        if direction == "BULLISH":
            levels = sr_data.get("resistance_levels", [])
        else:
            levels = sr_data.get("support_levels", [])
        # Find the broken level's fractal score
        broken = breakout_data.get("broken_level", 0)
        for lvl in levels:
            if abs(lvl.get("level", 0) - broken) / max(broken, 1) < 0.005:
                sr_quality = lvl.get("fractal_score", 0.5)
                break

    # Component 5: Volume & body (from original breakout)
    vol_score = min(1, breakout_data.get("volume_ratio", 0) / 2.0)
    body_score = min(1, breakout_data.get("body_ratio", 0) / 1.5)
    classical_score = 0.6 * vol_score + 0.4 * body_score

    # Composite quality score (weighted)
    quality_score = (
        0.25 * hurst_score
        + 0.15 * spectrum_score
        + 0.20 * mfdcca_score
        + 0.15 * sr_quality
        + 0.25 * classical_score
    )
    quality_score = round(min(1.0, quality_score), 3)

    # Signal classification
    if quality_score >= 0.7:
        quality_signal = "STRONG_BUY" if breakout_data["direction"] == "BULLISH" else "STRONG_SELL"
    elif quality_score >= 0.5:
        quality_signal = "BUY" if breakout_data["direction"] == "BULLISH" else "SELL"
    elif quality_score >= 0.3:
        quality_signal = "WATCH"
    else:
        quality_signal = "AVOID"

    result["quality_score"] = quality_score
    result["quality_components"] = {
        "hurst_persistence": round(hurst_score, 3),
        "spectrum_stability": round(spectrum_score, 3),
        "mfdcca_alignment": round(mfdcca_score, 3),
        "sr_fractal_quality": round(sr_quality, 3),
        "classical_confirmation": round(classical_score, 3),
    }
    result["quality_signal"] = quality_signal
    result["hurst"] = round(hurst, 4)
    result["spectral_width"] = round(spectral_width, 4)

    return result
