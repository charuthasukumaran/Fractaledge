"""
Topless Target Engine — Detects open-ended runner setups.
----------------------------------------------------------
A "topless target" occurs when:
  1. Price breaks above ALL visible resistance levels
  2. The multifractal regime supports trend continuation (h > 0.5, narrowing spectrum)
  3. MFDCCA shows alignment with broader market (or deliberate decoupling)
  4. No overhead resistance = open-ended upside potential

This module identifies and scores these setups.
"""
import numpy as np
from typing import Optional


def detect_topless_target(
    candles: list,
    sr_data: dict,
    mfdfa_result: dict,
    breakout_data: dict,
    mfdcca_result: dict = None,
    scale_analysis: dict = None,
) -> dict:
    """
    Detect topless target (open-ended runner) setups.

    Criteria:
      1. Price is above ALL resistance levels (topless condition)
      2. Hurst h(2) > 0.5 (persistent trend)
      3. Breakout has been detected or price is in discovery mode
      4. Volume confirms the move

    Returns:
        {
            "is_topless": bool,
            "topless_score": float (0-1),
            "price_discovery": bool,
            "all_time_high_proximity": float,
            "trailing_stop": float,
            "components": { ... },
            "strategy": str,
        }
    """
    empty = {
        "is_topless": False,
        "topless_score": 0.0,
        "price_discovery": False,
        "all_time_high_proximity": 0.0,
        "trailing_stop": 0.0,
        "components": {},
        "strategy": "No topless setup detected.",
    }

    if not candles or len(candles) < 20:
        return empty

    current_price = float(candles[-1]["close"])
    highs = np.array([c["high"] for c in candles], dtype=float)
    all_time_high = float(np.max(highs))
    ath_proximity = current_price / all_time_high if all_time_high > 0 else 0

    # ── Condition 1: Topless check (no overhead resistance) ────────
    resistance_levels = sr_data.get("resistance_levels", [])
    has_overhead_resistance = any(
        r.get("level", 0) > current_price * 1.001 for r in resistance_levels
    )
    topless_condition = not has_overhead_resistance or ath_proximity > 0.995

    # ── Condition 2: Fractal persistence ──────────────────────────
    hurst = mfdfa_result.get("hurst", 0.5)
    if np.isnan(hurst):
        hurst = 0.5
    spectral_width = mfdfa_result.get("spectral_width", 0.5)

    # Persistent trend: h > 0.5
    persistence_score = max(0, min(1, (hurst - 0.4) / 0.25))

    # Stable regime: narrow spectrum
    stability_score = max(0, min(1, 1.0 - (spectral_width - 0.1) / 0.5))

    # ── Condition 3: Momentum confirmation ────────────────────────
    # Price above recent highs (last 20 bars)
    recent_high = float(np.max(highs[-20:]))
    momentum_score = 1.0 if current_price >= recent_high * 0.998 else max(0, current_price / recent_high)

    # Volume confirmation
    volumes = np.array([c.get("volume", 0) for c in candles], dtype=float)
    if len(volumes) > 20:
        avg_vol = np.mean(volumes[-21:-1])
        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
        volume_score = min(1.0, vol_ratio / 1.5)
    else:
        volume_score = 0.5

    # ── Condition 4: MFDCCA alignment ─────────────────────────────
    coupling_score = 0.5
    if mfdcca_result and "error" not in mfdcca_result:
        rho_q = mfdcca_result.get("rho_q", {})
        rho_2 = abs(rho_q.get(2, 0.0))
        # High correlation = sector alignment (reliable topless)
        # Low correlation = decoupled alpha play (risky but potentially stronger)
        coupling_score = rho_2

    # ── Composite topless score ───────────────────────────────────
    topless_score = (
        0.20 * (1.0 if topless_condition else 0.0)
        + 0.25 * persistence_score
        + 0.15 * stability_score
        + 0.20 * momentum_score
        + 0.10 * volume_score
        + 0.10 * coupling_score
    )
    topless_score = round(min(1.0, topless_score), 3)

    is_topless = topless_condition and topless_score >= 0.5
    price_discovery = topless_condition and ath_proximity > 0.99

    # ── Trailing stop calculation ─────────────────────────────────
    # ATR-based trailing stop (use recent volatility)
    if len(candles) >= 15:
        recent_highs = highs[-14:]
        recent_lows = np.array([c["low"] for c in candles[-14:]], dtype=float)
        recent_closes = np.array([c["close"] for c in candles[-14:]], dtype=float)
        trs = []
        for i in range(1, len(recent_closes)):
            hl = recent_highs[i] - recent_lows[i]
            hc = abs(recent_highs[i] - recent_closes[i - 1])
            lc = abs(recent_lows[i] - recent_closes[i - 1])
            trs.append(max(hl, hc, lc))
        atr = np.mean(trs) if trs else 0
        trailing_stop = round(current_price - 2.0 * atr, 2)
    else:
        trailing_stop = round(current_price * 0.97, 2)

    # Nearest support as alternative stop
    nearest_support = sr_data.get("nearest_support", 0)
    if nearest_support > 0 and nearest_support < current_price:
        trailing_stop = max(trailing_stop, round(nearest_support * 0.998, 2))

    # ── Strategy description ──────────────────────────────────────
    if is_topless and price_discovery:
        strategy = (
            f"PRICE DISCOVERY MODE — No overhead resistance. "
            f"Trail stop at {trailing_stop:.2f} (2x ATR below). "
            f"Hurst={hurst:.3f} supports trend continuation. "
            f"Let winners run — scale out at trailing stop only."
        )
    elif is_topless:
        strategy = (
            f"TOPLESS SETUP — Above all visible resistance. "
            f"Trail stop at {trailing_stop:.2f}. "
            f"Watch for volume expansion to confirm continuation. "
            f"Partial profit at +2% increments, hold core position."
        )
    elif topless_condition and topless_score >= 0.3:
        strategy = (
            f"POTENTIAL TOPLESS — Near ATH but fractal regime not fully supportive. "
            f"Wait for Hurst > 0.55 and narrowing spectrum before committing."
        )
    else:
        strategy = "No topless setup detected."

    return {
        "is_topless": is_topless,
        "topless_score": topless_score,
        "price_discovery": price_discovery,
        "all_time_high": round(all_time_high, 2),
        "all_time_high_proximity": round(ath_proximity, 4),
        "trailing_stop": trailing_stop,
        "current_price": round(current_price, 2),
        "components": {
            "topless_condition": topless_condition,
            "persistence": round(persistence_score, 3),
            "stability": round(stability_score, 3),
            "momentum": round(momentum_score, 3),
            "volume": round(volume_score, 3),
            "coupling": round(coupling_score, 3),
        },
        "hurst": round(hurst, 4),
        "spectral_width": round(spectral_width, 4),
        "strategy": strategy,
    }
