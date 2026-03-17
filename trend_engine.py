"""
Trend Engine — Technical indicators for market trend and momentum analysis.
---------------------------------------------------------------------------
Pure numpy implementation. No pandas/ta-lib dependencies.
Provides EMA, RSI, MACD, ATR, and trend direction classification.
"""
import numpy as np
from typing import Optional


def compute_ema(data: np.ndarray, period: int) -> np.ndarray:
    """
    Exponential Moving Average.
    Seeded with SMA of first `period` values, then applies EMA formula.
    Returns array same length as input.
    """
    if len(data) < period:
        return np.full_like(data, np.nan, dtype=float)

    alpha = 2.0 / (period + 1)
    ema = np.empty_like(data, dtype=float)
    ema[:period] = np.nan
    ema[period - 1] = np.mean(data[:period])  # SMA seed

    for i in range(period, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]

    return ema


def compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    """
    Relative Strength Index using Wilder's smoothing.
    Returns latest RSI value (0-100).
    """
    if len(closes) < period + 1:
        return 50.0  # neutral default

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial average (SMA of first `period`)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    # Wilder's smoothing for remaining
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> dict:
    """
    MACD indicator.
    Returns latest values: {"macd": float, "signal": float, "histogram": float}
    """
    if len(closes) < slow + signal_period:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)

    # MACD line = fast EMA - slow EMA
    macd_line = ema_fast - ema_slow

    # Signal line = EMA of MACD line (starting from where slow EMA is valid)
    valid_start = slow - 1
    macd_valid = macd_line[valid_start:]
    signal_line = compute_ema(macd_valid, signal_period)

    macd_val = float(macd_valid[-1]) if len(macd_valid) > 0 else 0.0
    signal_val = float(signal_line[-1]) if len(signal_line) > 0 and not np.isnan(signal_line[-1]) else 0.0
    histogram = macd_val - signal_val

    return {
        "macd": round(macd_val, 2),
        "signal": round(signal_val, 2),
        "histogram": round(histogram, 2),
    }


def compute_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> float:
    """
    Average True Range using Wilder's smoothing.
    Returns latest ATR value.
    """
    if len(closes) < period + 1:
        return float(np.mean(highs - lows)) if len(highs) > 0 else 0.0

    # True Range = max(H-L, |H-prevC|, |L-prevC|)
    tr = np.empty(len(closes) - 1)
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i - 1] = max(hl, hc, lc)

    # Wilder's smoothing (similar to EMA with alpha=1/period)
    atr = np.mean(tr[:period])  # SMA seed
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period

    return round(float(atr), 2)


def _classify_trend(ema9: float, ema21: float, ema50: float) -> tuple:
    """
    Classify trend direction based on EMA alignment.
    Returns (trend_label, trend_strength).
    """
    if np.isnan(ema9) or np.isnan(ema21) or np.isnan(ema50):
        return "SIDEWAYS", 0.0

    # Check alignment
    up_aligned = ema9 > ema21 > ema50
    down_aligned = ema9 < ema21 < ema50

    if up_aligned:
        # Strength based on how spread apart the EMAs are
        spread = (ema9 - ema50) / ema50 * 100  # % spread
        strength = min(1.0, spread / 2.0)  # 2% spread = full strength
        return "UPTREND", round(strength, 3)
    elif down_aligned:
        spread = (ema50 - ema9) / ema50 * 100
        strength = min(1.0, spread / 2.0)
        return "DOWNTREND", round(strength, 3)
    else:
        return "SIDEWAYS", 0.0


def compute_trend(candles: list) -> dict:
    """
    Main entry point — compute all trend/momentum indicators.

    Args:
        candles: list of dicts with {open, high, low, close, volume, timestamp}

    Returns:
        Dict with all trend indicators:
        {
            "ema_9", "ema_21", "ema_50",
            "ema_9_series", "ema_21_series",  # for chart overlay
            "rsi_14", "rsi_signal",
            "macd", "macd_signal",
            "atr_14",
            "trend", "trend_strength"
        }
    """
    if not candles or len(candles) < 50:
        return {
            "ema_9": 0, "ema_21": 0, "ema_50": 0,
            "ema_9_series": [], "ema_21_series": [],
            "rsi_14": 50.0, "rsi_signal": "NEUTRAL",
            "macd": {"macd": 0, "signal": 0, "histogram": 0},
            "macd_signal": "NEUTRAL",
            "atr_14": 0,
            "trend": "SIDEWAYS", "trend_strength": 0.0,
        }

    closes = np.array([c["close"] for c in candles], dtype=float)
    highs = np.array([c["high"] for c in candles], dtype=float)
    lows = np.array([c["low"] for c in candles], dtype=float)

    # EMAs
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    ema50 = compute_ema(closes, 50)

    # Latest EMA values
    ema9_val = float(ema9[-1]) if not np.isnan(ema9[-1]) else 0.0
    ema21_val = float(ema21[-1]) if not np.isnan(ema21[-1]) else 0.0
    ema50_val = float(ema50[-1]) if not np.isnan(ema50[-1]) else 0.0

    # EMA series for chart (last 100 values, matched to candle chart)
    last_n = min(100, len(candles))
    ema9_series = [round(float(v), 2) if not np.isnan(v) else None for v in ema9[-last_n:]]
    ema21_series = [round(float(v), 2) if not np.isnan(v) else None for v in ema21[-last_n:]]

    # RSI
    rsi = compute_rsi(closes, 14)
    if rsi <= 30:
        rsi_signal = "OVERSOLD"
    elif rsi >= 70:
        rsi_signal = "OVERBOUGHT"
    else:
        rsi_signal = "NEUTRAL"

    # MACD
    macd = compute_macd(closes)
    if macd["histogram"] > 0 and macd["macd"] > macd["signal"]:
        macd_signal = "BULLISH"
    elif macd["histogram"] < 0 and macd["macd"] < macd["signal"]:
        macd_signal = "BEARISH"
    else:
        macd_signal = "NEUTRAL"

    # ATR
    atr = compute_atr(highs, lows, closes, 14)

    # Trend classification
    trend, trend_strength = _classify_trend(ema9_val, ema21_val, ema50_val)

    return {
        "ema_9": round(ema9_val, 2),
        "ema_21": round(ema21_val, 2),
        "ema_50": round(ema50_val, 2),
        "ema_9_series": ema9_series,
        "ema_21_series": ema21_series,
        "rsi_14": rsi,
        "rsi_signal": rsi_signal,
        "macd": macd,
        "macd_signal": macd_signal,
        "atr_14": atr,
        "trend": trend,
        "trend_strength": trend_strength,
    }
