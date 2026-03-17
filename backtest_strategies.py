"""
Backtest Strategies — Built-in trading strategies for the backtester.
Each strategy implements BaseStrategy with on_signal() returning an Action.
"""
from abc import ABC, abstractmethod
from typing import Optional


class Action:
    """Trade action constants returned by strategies."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"


class BaseStrategy(ABC):
    """Base class for all backtest strategies."""

    @abstractmethod
    def on_signal(self, signal: dict, candle: dict, position) -> str:
        """
        Evaluate a signal and decide action.

        Args:
            signal: full signal dict with regime_label, ensemble_score, and features
                    (features contains trend, breakout, risk, support_resistance, etc.)
            candle: current OHLCV candle dict
            position: current open Position object or None

        Returns:
            Action.BUY, Action.SELL, Action.HOLD, or Action.EXIT
        """
        pass

    @abstractmethod
    def get_params(self) -> dict:
        """Return tunable parameters for this strategy."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Return human-readable strategy name."""
        pass

    def get_description(self) -> str:
        """Return a short description of the strategy logic."""
        return ""

    def get_stoploss(self, signal: dict, candle: dict, direction: str) -> float:
        """
        Get stoploss price for a new position.
        Default: use risk engine output from the signal features.
        """
        features = signal.get("features", {})
        risk = features.get("risk", {})
        if direction == "LONG":
            sl = risk.get("long_trade", {}).get("stoploss")
            return sl if sl else candle["close"] * 0.98
        else:
            sl = risk.get("short_trade", {}).get("stoploss")
            return sl if sl else candle["close"] * 1.02

    def get_target(self, signal: dict, candle: dict, direction: str) -> float:
        """
        Get target price for a new position.
        Default: use risk engine target_2 from the signal features.
        """
        features = signal.get("features", {})
        risk = features.get("risk", {})
        if direction == "LONG":
            tgt = risk.get("long_trade", {}).get("target_2")
            return tgt if tgt else candle["close"] * 1.04
        else:
            tgt = risk.get("short_trade", {}).get("target_2")
            return tgt if tgt else candle["close"] * 0.96


# ══════════════════════════════════════════════════════════════════
# STRATEGY 1: REGIME-FOLLOWING
# ══════════════════════════════════════════════════════════════════

class RegimeStrategy(BaseStrategy):
    """
    Regime-Following Strategy:
    - BUY when regime is GREEN and trend is UPTREND
    - EXIT when regime changes to RED
    - Optionally SHORT when RED + DOWNTREND
    - EXIT longs in AMBER (strict mode) or hold through (relaxed mode)
    """

    def __init__(self, enter_on_green_only: bool = True, allow_shorts: bool = False):
        self.enter_on_green_only = enter_on_green_only
        self.allow_shorts = allow_shorts

    def on_signal(self, signal, candle, position) -> str:
        regime = signal.get("regime_label", "AMBER")
        features = signal.get("features", {})
        trend = features.get("trend", {}).get("trend", "SIDEWAYS")

        if position is None:
            # Entry logic
            if regime == "GREEN" and trend in ("UPTREND", "STRONG_UP"):
                return Action.BUY
            if self.allow_shorts and regime == "RED" and trend in ("DOWNTREND", "STRONG_DOWN"):
                return Action.SELL
            return Action.HOLD
        else:
            # Exit logic
            if position.direction == "LONG":
                if regime == "RED":
                    return Action.EXIT
                if regime == "AMBER" and self.enter_on_green_only:
                    return Action.EXIT  # strict: exit on any non-GREEN
                return Action.HOLD
            else:  # SHORT
                if regime == "GREEN":
                    return Action.EXIT
                return Action.HOLD

    def get_params(self) -> dict:
        return {
            "enter_on_green_only": self.enter_on_green_only,
            "allow_shorts": self.allow_shorts,
        }

    def get_name(self) -> str:
        return "Regime-Following"

    def get_description(self) -> str:
        return "Buy in GREEN regime with uptrend confirmation. Exit on RED. Adapts to market stress."


# ══════════════════════════════════════════════════════════════════
# STRATEGY 2: BREAKOUT QUALITY
# ══════════════════════════════════════════════════════════════════

class BreakoutStrategy(BaseStrategy):
    """
    Breakout Quality Strategy:
    - BUY on breakout with quality_signal BUY or STRONG_BUY
    - Exit on regime RED or opposing breakout signal
    - Uses fractal-aware quality scoring for entry filtering
    """

    def __init__(self, min_quality: float = 0.5, exit_on_regime_red: bool = True):
        self.min_quality = min_quality
        self.exit_on_regime_red = exit_on_regime_red

    def on_signal(self, signal, candle, position) -> str:
        features = signal.get("features", {})
        bq = features.get("breakout_quality", features.get("breakout", {}))
        quality_score = bq.get("quality_score", 0)
        quality_signal = bq.get("quality_signal", "")
        breakout_detected = bq.get("breakout_detected", False)
        direction = bq.get("direction", "")
        regime = signal.get("regime_label", "AMBER")

        if position is None:
            # Entry on quality breakout
            if breakout_detected and quality_score >= self.min_quality:
                if quality_signal in ("STRONG_BUY", "BUY") or direction == "BULLISH":
                    return Action.BUY
                if quality_signal in ("STRONG_SELL", "SELL") or direction == "BEARISH":
                    return Action.SELL
            return Action.HOLD
        else:
            # Exit logic
            if self.exit_on_regime_red and regime == "RED":
                return Action.EXIT
            # Exit long on bearish breakout, vice versa
            if position.direction == "LONG" and breakout_detected and direction == "BEARISH":
                return Action.EXIT
            if position.direction == "SHORT" and breakout_detected and direction == "BULLISH":
                return Action.EXIT
            return Action.HOLD

    def get_params(self) -> dict:
        return {
            "min_quality": self.min_quality,
            "exit_on_regime_red": self.exit_on_regime_red,
        }

    def get_name(self) -> str:
        return "Breakout Quality"

    def get_description(self) -> str:
        return "Enter on high-quality fractal-validated breakouts. Exit on regime stress or reversal."


# ══════════════════════════════════════════════════════════════════
# STRATEGY 3: TREND-FOLLOWING
# ══════════════════════════════════════════════════════════════════

class TrendFollowingStrategy(BaseStrategy):
    """
    Trend + Regime Confirmation Strategy:
    - BUY when UPTREND + GREEN/AMBER regime + RSI not overbought
    - SHORT when DOWNTREND + RED regime + RSI not oversold
    - EXIT on trend reversal or regime conflict
    """

    def __init__(self, rsi_overbought: float = 70, rsi_oversold: float = 30):
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def on_signal(self, signal, candle, position) -> str:
        features = signal.get("features", {})
        trend_data = features.get("trend", {})
        trend_dir = trend_data.get("trend", "SIDEWAYS")
        rsi = trend_data.get("rsi_14", 50)
        regime = signal.get("regime_label", "AMBER")

        if position is None:
            # Entry: trend + regime alignment + RSI filter
            if trend_dir in ("UPTREND", "STRONG_UP") and regime in ("GREEN", "AMBER") and rsi < self.rsi_overbought:
                return Action.BUY
            if trend_dir in ("DOWNTREND", "STRONG_DOWN") and regime == "RED" and rsi > self.rsi_oversold:
                return Action.SELL
            return Action.HOLD
        else:
            # Exit on trend reversal or regime conflict
            if position.direction == "LONG":
                if trend_dir in ("DOWNTREND", "STRONG_DOWN") or regime == "RED":
                    return Action.EXIT
                if rsi > self.rsi_overbought:
                    return Action.EXIT  # take profit on overbought
            else:  # SHORT
                if trend_dir in ("UPTREND", "STRONG_UP") or regime == "GREEN":
                    return Action.EXIT
                if rsi < self.rsi_oversold:
                    return Action.EXIT  # take profit on oversold
            return Action.HOLD

    def get_params(self) -> dict:
        return {
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
        }

    def get_name(self) -> str:
        return "Trend-Following"

    def get_description(self) -> str:
        return "Enter on trend + regime alignment with RSI filter. Exit on reversal or regime conflict."


# ══════════════════════════════════════════════════════════════════
# STRATEGY 4: MEAN REVERSION
# ══════════════════════════════════════════════════════════════════

class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy:
    - BUY when RSI oversold in GREEN regime (dip buying in calm market)
    - EXIT when RSI returns to neutral or overbought
    - Only trades long (mean reversion works best with buying dips)
    - Safety exit on RED regime
    """

    def __init__(self, rsi_entry: float = 30, rsi_exit: float = 55):
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit

    def on_signal(self, signal, candle, position) -> str:
        features = signal.get("features", {})
        rsi = features.get("trend", {}).get("rsi_14", 50)
        regime = signal.get("regime_label", "AMBER")

        if position is None:
            # Entry: oversold RSI in calm market
            if rsi <= self.rsi_entry and regime == "GREEN":
                return Action.BUY
            return Action.HOLD
        else:
            # Exit: RSI recovery or safety exit
            if rsi >= self.rsi_exit:
                return Action.EXIT
            if regime == "RED":
                return Action.EXIT  # safety exit on high stress
            return Action.HOLD

    def get_params(self) -> dict:
        return {
            "rsi_entry": self.rsi_entry,
            "rsi_exit": self.rsi_exit,
        }

    def get_name(self) -> str:
        return "Mean Reversion"

    def get_description(self) -> str:
        return "Buy oversold dips in calm (GREEN) markets. Exit on RSI recovery. Safety exit on RED."


# ══════════════════════════════════════════════════════════════════
# STRATEGY REGISTRY
# ══════════════════════════════════════════════════════════════════

STRATEGIES = {
    "regime": RegimeStrategy,
    "breakout": BreakoutStrategy,
    "trend": TrendFollowingStrategy,
    "mean_reversion": MeanReversionStrategy,
}
