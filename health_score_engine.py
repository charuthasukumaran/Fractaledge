"""
Market Health Score Engine
---------------------------
Produces a single 0-100 score analogous to a credit score.
Components: regime (40%), Hurst alignment (20%), coupling (15%), RSI (10%), volatility (15%).
"""
import json
from database import get_latest_signal, get_candles


def compute_health_score(symbol: str = "^NSEI") -> dict:
    """
    Compute a 0-100 Market Health Score from the latest signal data.

    Returns:
        {
            "score": int, "grade": str, "label": str,
            "components": {...}, "summary": str,
        }
    """
    signal = get_latest_signal(symbol=symbol)
    if not signal:
        return {"score": 0, "grade": "?", "label": "No Data", "components": {}, "summary": "No analysis data available."}

    features = {}
    if signal.get("features_json"):
        try:
            features = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    regime = signal.get("regime_label", "AMBER")
    hurst = signal.get("hurst_exponent", 0.5)
    coupling = signal.get("coupling_score", 0.5)
    trend_data = features.get("trend", {})
    rsi = trend_data.get("rsi_14", 50)
    atr = trend_data.get("atr_14", 0)
    trend_dir = trend_data.get("trend", "SIDEWAYS")

    # Get latest price for ATR ratio
    candles = get_candles(limit=1, symbol=symbol)
    price = candles[-1]["close"] if candles else 1

    # ── Component 1: Regime (40%) ──
    regime_map = {"GREEN": 100, "AMBER": 50, "RED": 10}
    regime_score = regime_map.get(regime, 50)
    regime_detail = {"GREEN": "Calm market", "AMBER": "Caution advised", "RED": "High stress"}.get(regime, "Unknown")

    # ── Component 2: Hurst Alignment (20%) ──
    if trend_dir == "UPTREND" and hurst > 0.5:
        hurst_score = min(100, 70 + (hurst - 0.5) * 200)
        hurst_detail = f"Trending (h={hurst:.3f}) — good for trend followers"
    elif trend_dir == "DOWNTREND" and hurst > 0.5:
        hurst_score = max(20, 60 - (hurst - 0.5) * 200)
        hurst_detail = f"Persistent downtrend (h={hurst:.3f}) — bearish pressure"
    else:
        hurst_score = max(0, 100 - abs(hurst - 0.5) * 400)
        hurst_detail = f"h={hurst:.3f} — {'near efficient' if abs(hurst - 0.5) < 0.05 else 'moderate deviation'}"
    hurst_score = max(0, min(100, hurst_score))

    # ── Component 3: Coupling (15%) ──
    coupling_score = max(0, min(100, 100 - abs(coupling - 0.5) * 200))
    if coupling > 0.7:
        coupling_detail = f"Very high coupling ({coupling:.3f}) — markets moving in lockstep"
    elif coupling < 0.2:
        coupling_detail = f"Low coupling ({coupling:.3f}) — divergence detected"
    else:
        coupling_detail = f"Moderate coupling ({coupling:.3f}) — healthy range"

    # ── Component 4: RSI Zone (10%) ──
    rsi_score = max(0, min(100, 100 - abs(rsi - 50) * 3.33))
    if rsi > 70:
        rsi_detail = f"RSI {rsi:.0f} — overbought territory"
    elif rsi < 30:
        rsi_detail = f"RSI {rsi:.0f} — oversold territory"
    else:
        rsi_detail = f"RSI {rsi:.0f} — healthy range"

    # ── Component 5: Volatility (15%) ──
    atr_pct = (atr / price * 100) if price > 0 else 0
    vol_score = max(0, min(100, 100 - atr_pct * 50))
    if atr_pct > 2:
        vol_detail = f"High volatility (ATR {atr_pct:.1f}% of price)"
    elif atr_pct > 1:
        vol_detail = f"Moderate volatility (ATR {atr_pct:.1f}%)"
    else:
        vol_detail = f"Low volatility (ATR {atr_pct:.1f}%) — calm conditions"

    # ── Weighted Total ──
    total = (
        regime_score * 0.40
        + hurst_score * 0.20
        + coupling_score * 0.15
        + rsi_score * 0.10
        + vol_score * 0.15
    )
    score = max(0, min(100, int(round(total))))

    # Grade & label
    if score >= 90:
        grade, label = "A+", "Excellent"
    elif score >= 80:
        grade, label = "A", "Very Good"
    elif score >= 70:
        grade, label = "B+", "Good"
    elif score >= 60:
        grade, label = "B", "Fair"
    elif score >= 50:
        grade, label = "C", "Caution"
    elif score >= 30:
        grade, label = "D", "Poor"
    else:
        grade, label = "F", "Critical"

    # Summary
    summaries = {
        "A+": f"{symbol} is in excellent health — calm regime, balanced indicators. Favorable conditions for trading.",
        "A": f"{symbol} shows very good market health. Conditions are supportive with minor concerns.",
        "B+": f"{symbol} is in good shape overall but watch for developing stress indicators.",
        "B": f"{symbol} health is fair — mixed signals. Proceed with normal caution.",
        "C": f"{symbol} shows caution signs — some stress building. Consider reducing position sizes.",
        "D": f"{symbol} health is poor — elevated stress across multiple indicators. Be defensive.",
        "F": f"{symbol} is in critical condition — high stress, extreme readings. Preserve capital.",
    }

    return {
        "score": score,
        "grade": grade,
        "label": label,
        "components": {
            "regime": {"score": int(regime_score), "weight": 40, "detail": regime_detail},
            "hurst_alignment": {"score": int(hurst_score), "weight": 20, "detail": hurst_detail},
            "coupling": {"score": int(coupling_score), "weight": 15, "detail": coupling_detail},
            "rsi_zone": {"score": int(rsi_score), "weight": 10, "detail": rsi_detail},
            "volatility": {"score": int(vol_score), "weight": 15, "detail": vol_detail},
        },
        "summary": summaries.get(grade, ""),
        "symbol": symbol,
    }
