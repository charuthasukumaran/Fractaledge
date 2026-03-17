"""
Risk Engine — computes stoploss, targets, and risk-reward ratios.
------------------------------------------------------------------
Uses ATR-based stops, S/R-based stops, and multi-target projections.
Adapts to the current regime (tighter stops in RED).
"""
from typing import Optional


def compute_risk_levels(
    current_price: float,
    atr: float,
    sr_data: dict,
    trend_data: dict,
    regime_label: str = "GREEN",
) -> dict:
    """
    Compute stoploss, targets, and risk-reward for both long and short trades.

    Args:
        current_price: latest close price
        atr:           ATR(14) value from trend_engine
        sr_data:       output from compute_support_resistance()
        trend_data:    output from compute_trend()
        regime_label:  current regime ("GREEN" / "AMBER" / "RED")

    Returns:
        {
            "long_trade":  { stoploss, stoploss_pct, target_1/2/3, risk_reward },
            "short_trade": { stoploss, stoploss_pct, target_1/2/3, risk_reward },
            "atr_14":           float,
            "suggested_risk_pct": float,
            "regime_note":      str,
        }
    """
    if current_price <= 0 or atr <= 0:
        zero_trade = {
            "stoploss": 0, "stoploss_pct": 0,
            "target_1": 0, "target_2": 0, "target_3": 0,
            "risk_reward": "N/A",
        }
        return {
            "long_trade": zero_trade,
            "short_trade": zero_trade,
            "atr_14": atr,
            "suggested_risk_pct": 1.0,
            "regime_note": "Insufficient data for risk calculation.",
        }

    # ── ATR multiplier by regime ───────────────────────────────────
    if regime_label == "RED":
        atr_mult = 1.0       # tighter stop in high-stress
        risk_pct = 0.5       # risk only 0.5% in RED
        regime_note = "RED regime — use tight stops and reduce position size."
    elif regime_label == "AMBER":
        atr_mult = 1.25
        risk_pct = 1.0
        regime_note = "AMBER regime — moderate caution, standard position sizing."
    else:  # GREEN
        atr_mult = 1.5
        risk_pct = 1.5
        regime_note = "GREEN regime — calm market, normal risk parameters."

    # ── Nearest S/R ────────────────────────────────────────────────
    nearest_support = sr_data.get("nearest_support", 0)
    nearest_resistance = sr_data.get("nearest_resistance", 0)

    support_levels = sr_data.get("support_levels", [])
    resistance_levels = sr_data.get("resistance_levels", [])

    # ── LONG TRADE ─────────────────────────────────────────────────
    # Stoploss: tighter of ATR-based or just below nearest support
    atr_sl_long = current_price - (atr * atr_mult)
    sr_sl_long = (nearest_support - atr * 0.2) if nearest_support > 0 else atr_sl_long
    # Use whichever is closer to price (tighter stop)
    long_sl = max(atr_sl_long, sr_sl_long) if sr_sl_long > 0 else atr_sl_long
    # But never above current price
    long_sl = min(long_sl, current_price * 0.998)
    long_sl = round(long_sl, 2)

    long_risk = current_price - long_sl
    long_sl_pct = round((long_risk / current_price) * 100, 2) if current_price > 0 else 0

    # Target 1: nearest resistance (or 1:1 risk if no resistance)
    if nearest_resistance > current_price:
        long_t1 = round(nearest_resistance, 2)
    else:
        long_t1 = round(current_price + long_risk, 2)  # 1:1

    # Target 2: 2x risk above entry
    long_t2 = round(current_price + 2 * long_risk, 2)

    # Target 3: 3x risk above entry
    long_t3 = round(current_price + 3 * long_risk, 2)

    # Risk:Reward ratio (to nearest target 1)
    long_reward = long_t1 - current_price
    long_rr = f"1:{round(long_reward / long_risk, 1)}" if long_risk > 0 else "N/A"

    # ── SHORT TRADE ────────────────────────────────────────────────
    atr_sl_short = current_price + (atr * atr_mult)
    sr_sl_short = (nearest_resistance + atr * 0.2) if nearest_resistance > 0 else atr_sl_short
    # Use whichever is closer to price (tighter stop)
    short_sl = min(atr_sl_short, sr_sl_short) if sr_sl_short > 0 else atr_sl_short
    # But never below current price
    short_sl = max(short_sl, current_price * 1.002)
    short_sl = round(short_sl, 2)

    short_risk = short_sl - current_price
    short_sl_pct = round((short_risk / current_price) * 100, 2) if current_price > 0 else 0

    # Target 1: nearest support (or 1:1 risk if no support)
    if nearest_support > 0 and nearest_support < current_price:
        short_t1 = round(nearest_support, 2)
    else:
        short_t1 = round(current_price - short_risk, 2)  # 1:1

    # Target 2/3
    short_t2 = round(current_price - 2 * short_risk, 2)
    short_t3 = round(current_price - 3 * short_risk, 2)

    short_reward = current_price - short_t1
    short_rr = f"1:{round(short_reward / short_risk, 1)}" if short_risk > 0 else "N/A"

    return {
        "long_trade": {
            "stoploss": long_sl,
            "stoploss_pct": long_sl_pct,
            "target_1": long_t1,
            "target_2": long_t2,
            "target_3": long_t3,
            "risk_reward": long_rr,
        },
        "short_trade": {
            "stoploss": short_sl,
            "stoploss_pct": short_sl_pct,
            "target_1": short_t1,
            "target_2": short_t2,
            "target_3": short_t3,
            "risk_reward": short_rr,
        },
        "atr_14": round(atr, 2),
        "suggested_risk_pct": risk_pct,
        "regime_note": regime_note,
    }
