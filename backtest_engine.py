"""
Backtesting Engine — Run trading strategies against historical data.
Supports fast mode (pre-computed signals from DB) and full mode (recompute from candles).
"""
import json
import logging
import time
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

from config import config
from database import get_candles, get_signals
from backtest_strategies import Action

logger = logging.getLogger(__name__)


# ── Data Structures ───────────────────────────────────────────────

@dataclass
class Position:
    """Represents an open trading position."""
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    entry_time: str
    quantity: float
    stoploss: float
    target: float
    entry_signal: dict
    regime_at_entry: str
    entry_bar: int = 0


@dataclass
class Trade:
    """Represents a completed (closed) trade."""
    direction: str
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    quantity: float
    pnl: float
    pnl_pct: float
    regime_at_entry: str
    regime_at_exit: str
    exit_reason: str        # "signal", "stoploss", "target", "strategy_exit"
    bars_held: int


@dataclass
class BacktestResult:
    """Complete backtest output with all metrics."""
    strategy_name: str
    symbol: str
    mode: str               # "fast" or "full"
    start_time: str
    end_time: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    avg_trade_pnl: float
    avg_winner: float
    avg_loser: float
    largest_winner: float
    largest_loser: float
    avg_bars_held: float
    trades: list
    equity_curve: list
    regime_breakdown: dict
    signals_evaluated: int
    computation_time_seconds: float
    params: dict


# ── Core Backtester ───────────────────────────────────────────────

class Backtester:
    """
    Main backtesting engine.

    Usage:
        bt = Backtester(symbol="^NSEI", strategy=RegimeStrategy())
        result = bt.run_fast(days=30)       # use DB signals
        result = bt.run_full(days=30)       # recompute signals
    """

    def __init__(
        self,
        symbol: str = "^NSEI",
        strategy=None,
        initial_capital: float = None,
        commission_pct: float = None,
        slippage_pct: float = None,
    ):
        self.symbol = symbol
        self.strategy = strategy
        self.initial_capital = initial_capital or config.backtest.initial_capital
        self.commission_pct = commission_pct if commission_pct is not None else config.backtest.commission_pct
        self.slippage_pct = slippage_pct if slippage_pct is not None else config.backtest.slippage_pct

        # State — reset for each run
        self.capital = self.initial_capital
        self.position: Optional[Position] = None
        self.trades: list = []
        self.equity_curve: list = []
        self.signals_evaluated = 0

    def _reset(self):
        """Reset state for a new backtest run."""
        self.capital = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.signals_evaluated = 0

    # ── Fast Mode ─────────────────────────────────────────────────

    def run_fast(self, days: int = 30) -> BacktestResult:
        """
        Fast mode: use pre-computed signals from the database.
        Signals must already exist (from live/demo mode).
        """
        self._reset()
        start_time = time.time()

        logger.info(f"[Backtest] Fast mode: {self.strategy.get_name()} on {self.symbol} ({days} days)")

        # Load signals with features
        signals = get_signals(limit=5000, symbol=self.symbol)
        if not signals:
            logger.error(f"No signals found for {self.symbol}. Run 'live' or 'demo' mode first.")
            return self._empty_result("fast", time.time() - start_time)

        # Filter by date range
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        signals = [s for s in signals if s.get("timestamp", "") >= cutoff]

        if len(signals) < 2:
            logger.error(f"Not enough signals ({len(signals)}) for backtesting.")
            return self._empty_result("fast", time.time() - start_time)

        # Load candles for price data (for stoploss/target checking)
        candles = get_candles(limit=10000, symbol=self.symbol)
        candle_map = {c["timestamp"]: c for c in candles}

        logger.info(f"[Backtest] Loaded {len(signals)} signals, {len(candles)} candles")

        # Iterate through signals
        for i, sig in enumerate(signals):
            ts = sig.get("timestamp", "")
            candle = candle_map.get(ts)
            if not candle:
                # Find nearest candle
                candle = self._find_nearest_candle(ts, candles)
            if not candle:
                continue

            # Parse features from JSON
            signal = self._parse_signal(sig)

            self._process_bar(candle, signal, i)
            self.signals_evaluated += 1

        # Close any open position at the end
        if self.position and candles:
            last_candle = candles[-1]
            self._close_position(last_candle, "end_of_data",
                                 regime=signals[-1].get("regime_label", "AMBER"))

        comp_time = time.time() - start_time
        logger.info(f"[Backtest] Fast mode completed in {comp_time:.2f}s")
        return self._compute_metrics("fast", comp_time,
                                      signals[0].get("timestamp", ""),
                                      signals[-1].get("timestamp", ""))

    # ── Full Mode ─────────────────────────────────────────────────

    def run_full(self, days: int = 30, signal_step: int = None) -> BacktestResult:
        """
        Full mode: recompute signals from raw candles using all analysis engines.
        Slower but allows testing with different parameters.
        """
        self._reset()
        start_time = time.time()
        step = signal_step or config.backtest.signal_step

        logger.info(f"[Backtest] Full mode: {self.strategy.get_name()} on {self.symbol} "
                     f"({days} days, step={step})")

        # Load all candles
        candles = get_candles(limit=20000, symbol=self.symbol)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        candles = [c for c in candles if c.get("timestamp", "") >= cutoff]

        if len(candles) < config.mfdfa.window_size:
            # Try loading more candles without date filter
            candles = get_candles(limit=20000, symbol=self.symbol)

        if len(candles) < config.mfdfa.min_bars:
            logger.error(f"Not enough candles ({len(candles)}) for full backtest. Need at least {config.mfdfa.min_bars}.")
            return self._empty_result("full", time.time() - start_time)

        close_prices = np.array([c["close"] for c in candles])
        window = config.mfdfa.window_size

        # Load secondary prices for MFDCCA (optional)
        secondary_prices = self._load_secondary_prices(days)

        logger.info(f"[Backtest] Processing {len(candles)} candles (window={window}, step={step})")

        # Iterate through candles with rolling window
        total_steps = 0
        for i in range(window, len(close_prices), step):
            window_prices = close_prices[max(0, i - window):i]
            candle_window = candles[max(0, i - window):i]
            current_candle = candles[i - 1]

            sec_window = None
            if secondary_prices is not None and i <= len(secondary_prices):
                sec_window = secondary_prices[max(0, i - window):i]

            # Compute full signal from candles
            try:
                signal = self._compute_signal_from_candles(
                    candle_window, window_prices, sec_window
                )
            except Exception as e:
                logger.debug(f"Signal computation error at bar {i}: {e}")
                continue

            self._process_bar(current_candle, signal, total_steps)
            self.signals_evaluated += 1
            total_steps += 1

            if total_steps % 100 == 0:
                elapsed = time.time() - start_time
                logger.info(f"[Backtest] Processed {total_steps} signals ({elapsed:.1f}s elapsed)")

        # Close any open position
        if self.position and candles:
            last_candle = candles[-1]
            self._close_position(last_candle, "end_of_data", regime="AMBER")

        comp_time = time.time() - start_time
        logger.info(f"[Backtest] Full mode completed in {comp_time:.2f}s ({total_steps} signals)")

        start_ts = candles[window].get("timestamp", "") if len(candles) > window else ""
        end_ts = candles[-1].get("timestamp", "") if candles else ""
        return self._compute_metrics("full", comp_time, start_ts, end_ts)

    # ── Bar Processing ────────────────────────────────────────────

    def _process_bar(self, candle: dict, signal: dict, bar_index: int):
        """Process a single bar: check stops/targets, get strategy action, execute."""
        price = candle.get("close", 0)
        high = candle.get("high", price)
        low = candle.get("low", price)
        regime = signal.get("regime_label", "AMBER")

        # Record equity
        equity = self.capital
        if self.position:
            if self.position.direction == "LONG":
                unrealized = (price - self.position.entry_price) * self.position.quantity
            else:
                unrealized = (self.position.entry_price - price) * self.position.quantity
            equity += unrealized

        self.equity_curve.append({
            "timestamp": candle.get("timestamp", ""),
            "equity": round(equity, 2),
            "price": price,
            "regime": regime,
        })

        # Check stoploss / target for open position
        if self.position:
            hit_stop = False
            hit_target = False

            if self.position.direction == "LONG":
                hit_stop = low <= self.position.stoploss
                hit_target = high >= self.position.target
            else:  # SHORT
                hit_stop = high >= self.position.stoploss
                hit_target = low <= self.position.target

            if hit_stop:
                exit_price = self.position.stoploss
                self._close_position_at_price(exit_price, candle, "stoploss", regime)
                return
            if hit_target:
                exit_price = self.position.target
                self._close_position_at_price(exit_price, candle, "target", regime)
                return

        # Get strategy action
        action = self.strategy.on_signal(signal, candle, self.position)

        # Execute action
        if action == Action.BUY and self.position is None:
            self._execute_entry(candle, signal, "LONG", bar_index)
        elif action == Action.SELL and self.position is None:
            self._execute_entry(candle, signal, "SHORT", bar_index)
        elif action == Action.EXIT and self.position is not None:
            self._close_position(candle, "strategy_exit", regime)

    def _execute_entry(self, candle: dict, signal: dict, direction: str, bar_index: int):
        """Open a new position."""
        price = candle["close"]

        # Apply slippage
        if direction == "LONG":
            entry_price = price * (1 + self.slippage_pct / 100)
        else:
            entry_price = price * (1 - self.slippage_pct / 100)

        # Calculate position size based on risk
        stoploss = self.strategy.get_stoploss(signal, candle, direction)
        target = self.strategy.get_target(signal, candle, direction)

        risk_per_share = abs(entry_price - stoploss)
        if risk_per_share <= 0:
            risk_per_share = entry_price * 0.02  # fallback: 2% risk

        risk_amount = self.capital * (config.backtest.risk_per_trade_pct / 100)
        quantity = risk_amount / risk_per_share
        quantity = max(1, int(quantity))

        # Check if we can afford it
        cost = entry_price * quantity
        commission = cost * (self.commission_pct / 100)
        total_cost = cost + commission

        if total_cost > self.capital:
            quantity = max(1, int((self.capital * 0.95) / entry_price))
            cost = entry_price * quantity
            commission = cost * (self.commission_pct / 100)

        self.capital -= commission  # deduct commission

        self.position = Position(
            direction=direction,
            entry_price=round(entry_price, 2),
            entry_time=candle.get("timestamp", ""),
            quantity=quantity,
            stoploss=round(stoploss, 2),
            target=round(target, 2),
            entry_signal=signal,
            regime_at_entry=signal.get("regime_label", "AMBER"),
            entry_bar=bar_index,
        )

        logger.debug(f"[Backtest] {direction} entry at {entry_price:.2f} qty={quantity} "
                      f"SL={stoploss:.2f} TGT={target:.2f}")

    def _close_position(self, candle: dict, reason: str, regime: str = "AMBER"):
        """Close the current position at candle close price."""
        if not self.position:
            return
        self._close_position_at_price(candle["close"], candle, reason, regime)

    def _close_position_at_price(self, exit_price: float, candle: dict, reason: str, regime: str):
        """Close position at a specific price (stoploss, target, or close)."""
        if not self.position:
            return

        pos = self.position

        # Apply slippage on exit
        if pos.direction == "LONG":
            actual_exit = exit_price * (1 - self.slippage_pct / 100)
            pnl = (actual_exit - pos.entry_price) * pos.quantity
        else:
            actual_exit = exit_price * (1 + self.slippage_pct / 100)
            pnl = (pos.entry_price - actual_exit) * pos.quantity

        # Deduct commission
        commission = actual_exit * pos.quantity * (self.commission_pct / 100)
        pnl -= commission
        pnl_pct = (pnl / (pos.entry_price * pos.quantity)) * 100

        self.capital += pnl  # add/subtract P&L

        # Estimate bars held
        bars_held = 0
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time)
            exit_dt = datetime.fromisoformat(candle.get("timestamp", pos.entry_time))
            bars_held = max(1, int((exit_dt - entry_dt).total_seconds() / 300))  # 5-min bars
        except (ValueError, TypeError):
            bars_held = 1

        trade = Trade(
            direction=pos.direction,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=round(actual_exit, 2),
            exit_time=candle.get("timestamp", ""),
            quantity=pos.quantity,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            regime_at_entry=pos.regime_at_entry,
            regime_at_exit=regime,
            exit_reason=reason,
            bars_held=bars_held,
        )
        self.trades.append(trade)
        self.position = None

        logger.debug(f"[Backtest] {pos.direction} exit at {actual_exit:.2f} "
                      f"PnL={pnl:.2f} ({pnl_pct:+.2f}%) reason={reason}")

    # ── Signal Computation (Full Mode) ────────────────────────────

    def _compute_signal_from_candles(self, candles: list, close_prices: np.ndarray,
                                      secondary_prices: np.ndarray = None) -> dict:
        """
        Run the complete signal pipeline on a candle window.
        Mirrors workers.compute_and_store_signal() without DB insert.
        """
        from feature_engine import run_feature_engine
        from trend_engine import compute_trend
        from sr_engine import compute_support_resistance, validate_sr_with_fractal
        from breakout_engine import detect_breakout, compute_breakout_quality
        from risk_engine import compute_risk_levels
        from mfdfa_engine import compute_mfdfa, compute_scale_analysis

        ts = candles[-1]["timestamp"] if candles else ""

        # Base signal: MFDFA + MFDCCA
        signal = run_feature_engine(close_prices, secondary_prices=secondary_prices, timestamp=ts)
        features = json.loads(signal.get("features_json", "{}"))

        # MFDFA detailed results
        mfdfa_result = compute_mfdfa(close_prices)
        scale_analysis = compute_scale_analysis(mfdfa_result)

        # Trend analysis
        trend_data = compute_trend(candles)

        # Support/Resistance with fractal validation
        sr_data = compute_support_resistance(candles)
        sr_data = validate_sr_with_fractal(sr_data, scale_analysis, mfdfa_result)

        # Breakout detection + quality scoring
        breakout_data = detect_breakout(
            candles, sr_data, trend_data,
            regime_label=signal.get("regime_label", "GREEN"),
        )
        breakout_quality = compute_breakout_quality(
            breakout_data, mfdfa_result,
            sr_data=sr_data, scale_analysis=scale_analysis,
        )

        # Risk engine
        risk_data = compute_risk_levels(
            current_price=close_prices[-1],
            atr=trend_data.get("atr_14", 0),
            sr_data=sr_data,
            trend_data=trend_data,
            regime_label=signal.get("regime_label", "GREEN"),
        )

        # Build features dict
        features["trend"] = trend_data
        features["support_resistance"] = sr_data
        features["breakout"] = breakout_data
        features["breakout_quality"] = breakout_quality
        features["risk"] = risk_data

        signal["features"] = features
        return signal

    # ── Helper Methods ────────────────────────────────────────────

    def _parse_signal(self, raw_signal: dict) -> dict:
        """Parse a signal from the database, extracting features from JSON."""
        signal = dict(raw_signal)
        features_json = signal.get("features_json", "{}")
        try:
            signal["features"] = json.loads(features_json) if features_json else {}
        except (json.JSONDecodeError, TypeError):
            signal["features"] = {}
        return signal

    def _find_nearest_candle(self, timestamp: str, candles: list) -> Optional[dict]:
        """Find the candle nearest to a given timestamp."""
        if not candles:
            return None
        # Binary search approach
        for c in candles:
            if c.get("timestamp", "") >= timestamp:
                return c
        return candles[-1]

    def _load_secondary_prices(self, days: int) -> Optional[np.ndarray]:
        """Load secondary prices for MFDCCA (e.g., BANKNIFTY for NIFTY)."""
        secondary_symbol = None
        if self.symbol in ("^NSEI", "NIFTY"):
            secondary_symbol = "^NSEBANK"
        elif self.symbol == "^NSEBANK":
            secondary_symbol = "^NSEI"

        if secondary_symbol:
            sec_candles = get_candles(limit=20000, symbol=secondary_symbol)
            if sec_candles and len(sec_candles) >= config.mfdfa.min_bars:
                return np.array([c["close"] for c in sec_candles])

        return None

    def _empty_result(self, mode: str, comp_time: float) -> BacktestResult:
        """Return an empty result when backtest can't run."""
        return BacktestResult(
            strategy_name=self.strategy.get_name() if self.strategy else "Unknown",
            symbol=self.symbol, mode=mode,
            start_time="", end_time="",
            initial_capital=self.initial_capital, final_capital=self.initial_capital,
            total_return_pct=0.0, sharpe_ratio=0.0,
            max_drawdown_pct=0.0, max_drawdown_duration=0,
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.0, profit_factor=0.0,
            avg_trade_pnl=0.0, avg_winner=0.0, avg_loser=0.0,
            largest_winner=0.0, largest_loser=0.0, avg_bars_held=0.0,
            trades=[], equity_curve=[], regime_breakdown={},
            signals_evaluated=0, computation_time_seconds=comp_time,
            params=self.strategy.get_params() if self.strategy else {},
        )

    # ── Metrics Computation ───────────────────────────────────────

    def _compute_metrics(self, mode: str, comp_time: float,
                          start_ts: str, end_ts: str) -> BacktestResult:
        """Compute all performance metrics from trade log and equity curve."""
        trades = self.trades
        total_trades = len(trades)

        # P&L arrays
        pnls = [t.pnl for t in trades]
        pnl_pcts = [t.pnl_pct for t in trades]
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]

        # Basic stats
        winning_trades = len(winners)
        losing_trades = len(losers)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        # P&L stats
        avg_trade_pnl = np.mean(pnls) if pnls else 0.0
        avg_winner = np.mean([t.pnl for t in winners]) if winners else 0.0
        avg_loser = np.mean([t.pnl for t in losers]) if losers else 0.0
        largest_winner = max([t.pnl for t in winners]) if winners else 0.0
        largest_loser = min([t.pnl for t in losers]) if losers else 0.0

        # Profit factor
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)

        # Avg bars held
        avg_bars_held = np.mean([t.bars_held for t in trades]) if trades else 0.0

        # Total return
        final_capital = self.capital
        total_return_pct = ((final_capital - self.initial_capital) / self.initial_capital) * 100

        # Sharpe ratio (annualized, assuming ~75 bars per day, 252 trading days)
        if len(pnl_pcts) > 1:
            returns_array = np.array(pnl_pcts) / 100
            sharpe = (np.mean(returns_array) / np.std(returns_array)) * np.sqrt(252) if np.std(returns_array) > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown from equity curve
        max_dd_pct = 0.0
        max_dd_duration = 0
        if self.equity_curve:
            equities = [e["equity"] for e in self.equity_curve]
            peak = equities[0]
            dd_start = 0
            for i, eq in enumerate(equities):
                if eq > peak:
                    peak = eq
                    dd_start = i
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                if dd > max_dd_pct:
                    max_dd_pct = dd
                    max_dd_duration = i - dd_start

        # Regime breakdown
        regime_breakdown = {}
        for regime in ("GREEN", "AMBER", "RED"):
            regime_trades = [t for t in trades if t.regime_at_entry == regime]
            regime_winners = [t for t in regime_trades if t.pnl > 0]
            regime_breakdown[regime] = {
                "trades": len(regime_trades),
                "win_rate": (len(regime_winners) / len(regime_trades) * 100) if regime_trades else 0.0,
                "avg_pnl": round(np.mean([t.pnl for t in regime_trades]), 2) if regime_trades else 0.0,
                "total_pnl": round(sum(t.pnl for t in regime_trades), 2) if regime_trades else 0.0,
            }

        # Convert trades to serializable dicts
        trade_dicts = []
        for t in trades:
            trade_dicts.append({
                "direction": t.direction,
                "entry_price": t.entry_price,
                "entry_time": t.entry_time,
                "exit_price": t.exit_price,
                "exit_time": t.exit_time,
                "quantity": t.quantity,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "regime_at_entry": t.regime_at_entry,
                "regime_at_exit": t.regime_at_exit,
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
            })

        return BacktestResult(
            strategy_name=self.strategy.get_name(),
            symbol=self.symbol,
            mode=mode,
            start_time=start_ts,
            end_time=end_ts,
            initial_capital=round(self.initial_capital, 2),
            final_capital=round(final_capital, 2),
            total_return_pct=round(total_return_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_drawdown_duration=max_dd_duration,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
            avg_trade_pnl=round(avg_trade_pnl, 2),
            avg_winner=round(avg_winner, 2),
            avg_loser=round(avg_loser, 2),
            largest_winner=round(largest_winner, 2),
            largest_loser=round(largest_loser, 2),
            avg_bars_held=round(avg_bars_held, 1),
            trades=trade_dicts,
            equity_curve=self.equity_curve,
            regime_breakdown=regime_breakdown,
            signals_evaluated=self.signals_evaluated,
            computation_time_seconds=round(comp_time, 2),
            params=self.strategy.get_params(),
        )
