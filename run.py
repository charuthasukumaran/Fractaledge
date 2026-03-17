#!/usr/bin/env python3
"""
FractalEdge — Main Entry Point
==========================================
Uses Yahoo Finance (free, no account needed).

Usage:
    python run.py demo       # Synthetic data, no internet needed
    python run.py live       # Real NIFTY data from Yahoo Finance
    python run.py backfill   # Just backfill data, don't start server
"""
import sys
import os
import argparse
import logging
import threading
import json
import numpy as np
import uvicorn

# ── Load .env file if present ──────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())
from datetime import datetime, timedelta

from config import config
from database import init_db, upsert_candles, get_candles, insert_signal
from feature_engine import run_feature_engine
from trend_engine import compute_trend
from sr_engine import compute_support_resistance, validate_sr_with_fractal
from breakout_engine import detect_breakout, compute_breakout_quality
from risk_engine import compute_risk_levels
from mfdfa_engine import compute_mfdfa, compute_mfdcca, compute_scale_analysis
from topless_engine import detect_topless_target
from workers import backfill_candles, compute_and_store_signal, run_worker_loop
from api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def generate_synthetic_candles(days=20, interval_minutes=5, seed=42, base_price=22500.0):
    """Generate realistic synthetic candles with regime-switching volatility."""
    bars_per_day = int(6.25 * 60 / interval_minutes)
    total_bars = bars_per_day * days

    np.random.seed(seed)
    price = base_price
    candles = []
    base_time = datetime.now() - timedelta(days=days)

    for i in range(total_bars):
        if i < total_bars * 0.3:
            vol = 0.0003
        elif i < total_bars * 0.6:
            vol = 0.0008
        elif i < total_bars * 0.8:
            vol = 0.0015
        else:
            vol = 0.0005

        drift = -0.00001 * (price - base_price) / base_price
        price *= (1 + drift + vol * np.random.randn())

        o = price * (1 + 0.0001 * np.random.randn())
        h = max(o, price) * (1 + abs(0.0002 * np.random.randn()))
        l = min(o, price) * (1 - abs(0.0002 * np.random.randn()))

        day_offset = i // bars_per_day
        bar_offset = i % bars_per_day
        ts = base_time + timedelta(days=day_offset)
        ts = ts.replace(hour=9, minute=15, second=0, microsecond=0)
        ts += timedelta(minutes=bar_offset * interval_minutes)
        while ts.weekday() >= 5:
            ts += timedelta(days=1)

        candles.append({
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(price, 2),
            "volume": int(np.random.randint(50000, 500000)),
            "source": "synthetic",
        })

    return candles


def run_demo():
    """Run with synthetic data — full fractal analysis pipeline."""
    logger.info("=" * 55)
    logger.info("  FRACTALEDGE - DEMO MODE")
    logger.info("  (Synthetic data, no internet needed)")
    logger.info("=" * 55)

    init_db()

    logger.info("Generating synthetic NIFTY candles...")
    candles = generate_synthetic_candles(days=20, seed=42, base_price=22500.0)
    upsert_candles(candles)
    logger.info(f"Inserted {len(candles)} synthetic NIFTY candles")

    logger.info("Generating synthetic BANKNIFTY candles for MFDCCA coupling...")
    banknifty_candles = generate_synthetic_candles(days=20, seed=99, base_price=48000.0)
    banknifty_close = np.array([c["close"] for c in banknifty_candles])
    logger.info(f"Generated {len(banknifty_candles)} synthetic BANKNIFTY candles")

    logger.info("Computing full fractal analysis pipeline...")
    all_candles = get_candles(limit=5000)
    close_prices = np.array([c["close"] for c in all_candles])
    window = config.mfdfa.window_size
    computed = 0

    for i in range(window, len(close_prices), 50):
        window_prices = close_prices[max(0, i - window):i]
        sec_window = banknifty_close[max(0, i - window):i] if i <= len(banknifty_close) else None
        ts = all_candles[i - 1]["timestamp"]

        signal = run_feature_engine(window_prices, secondary_prices=sec_window, timestamp=ts)

        try:
            features = json.loads(signal.get("features_json", "{}"))
            candle_window = all_candles[max(0, i - window):i]

            mfdfa_result = compute_mfdfa(window_prices)
            scale_analysis = compute_scale_analysis(mfdfa_result)
            features["scale_analysis"] = scale_analysis
            features["alpha"] = mfdfa_result.get("alpha", [])
            features["f_alpha"] = mfdfa_result.get("f_alpha", [])
            features["tau_q"] = {str(q): round(v, 4) for q, v in mfdfa_result.get("tau_q", {}).items()}
            features["scales"] = mfdfa_result.get("scales", [])

            mfdcca_result = None
            if sec_window is not None and len(sec_window) >= config.mfdfa.min_bars:
                mfdcca_result = compute_mfdcca(window_prices, sec_window)
                features["rho_q"] = {str(q): v for q, v in mfdcca_result.get("rho_q", {}).items()}
                features["rho_s"] = {str(s): v for s, v in mfdcca_result.get("rho_s", {}).items()}
                features["hq_x"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_x", {}).items() if not np.isnan(v)}
                features["hq_y"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_y", {}).items() if not np.isnan(v)}
                features["alpha_xy"] = mfdcca_result.get("alpha_xy", [])
                features["f_alpha_xy"] = mfdcca_result.get("f_alpha_xy", [])
                features["spectral_width_xy"] = mfdcca_result.get("spectral_width_xy", 0.0)

            trend_data = compute_trend(candle_window)
            sr_data = compute_support_resistance(candle_window)
            sr_data = validate_sr_with_fractal(sr_data, scale_analysis, mfdfa_result)

            breakout_data = detect_breakout(
                candle_window, sr_data, trend_data,
                regime_label=signal.get("regime_label", "GREEN"),
            )

            breakout_quality = compute_breakout_quality(
                breakout_data, mfdfa_result,
                mfdcca_result=mfdcca_result,
                sr_data=sr_data,
                scale_analysis=scale_analysis,
            )

            risk_data = compute_risk_levels(
                current_price=close_prices[i - 1],
                atr=trend_data.get("atr_14", 0),
                sr_data=sr_data,
                trend_data=trend_data,
                regime_label=signal.get("regime_label", "GREEN"),
            )

            topless_data = detect_topless_target(
                candle_window, sr_data, mfdfa_result, breakout_data,
                mfdcca_result=mfdcca_result,
                scale_analysis=scale_analysis,
            )

            features["trend"] = trend_data
            features["support_resistance"] = sr_data
            features["breakout"] = breakout_data
            features["breakout_quality"] = breakout_quality
            features["risk"] = risk_data
            features["topless_target"] = topless_data
            signal["features_json"] = json.dumps(features)
        except Exception as e:
            logger.error(f"Demo analysis error: {e}", exc_info=True)

        insert_signal(signal)
        computed += 1

    logger.info(f"Computed {computed} signals (full fractal pipeline)")
    _start_server()


def run_live():
    """Run with real NIFTY data from Yahoo Finance."""
    logger.info("=" * 55)
    logger.info("  FRACTALEDGE - LIVE MODE")
    logger.info("  (Real NIFTY data via Yahoo Finance)")
    logger.info("=" * 55)

    init_db()

    from data_client import MarketDataClient
    client = MarketDataClient()

    logger.info("Step 1: Backfilling historical candles...")
    backfill_candles(client, days=30)
    signal = compute_and_store_signal(client=client)
    if signal:
        logger.info(f"Initial regime: {signal['regime_label']} (score={signal['ensemble_score']:.3f})")

    logger.info("Step 2: Starting 5-minute update worker...")
    worker = threading.Thread(target=run_worker_loop, args=(client,), daemon=True)
    worker.start()

    _start_server()


def run_backfill():
    """Just backfill data without starting the server."""
    init_db()
    from data_client import MarketDataClient
    client = MarketDataClient()
    backfill_candles(client, days=30)
    compute_and_store_signal(client=client)
    logger.info("Backfill complete. Run 'python run.py live' to start the server.")


def _start_server():
    """Start the FastAPI server."""
    logger.info("")
    logger.info("=" * 55)
    logger.info("  FractalEdge Dashboard")
    logger.info("  http://localhost:%d", config.api_port)
    logger.info("=" * 55)
    logger.info("  API Endpoints:")
    logger.info("    /latest    - current regime + full analysis")
    logger.info("    /spectrum  - MFDFA multifractal spectrum")
    logger.info("    /mfdcca    - cross-correlation analysis")
    logger.info("    /analysis  - trend, S/R, breakout, risk, topless")
    logger.info("    /scanner   - breakout quality scanner")
    logger.info("    /topless   - topless target detection")
    logger.info("    /candles   - OHLCV data")
    logger.info("    /signals   - stress history")
    logger.info("")
    uvicorn.run(app, host=config.api_host, port=config.api_port)


def run_backtest(args):
    """Run a backtest with the specified strategy and parameters."""
    logger.info("=" * 55)
    logger.info("  FRACTALEDGE - BACKTEST MODE")
    logger.info("=" * 55)

    init_db()

    from backtest_strategies import STRATEGIES
    from backtest_engine import Backtester
    from backtest_report import print_report, to_json_file

    # Build strategy
    strategy_cls = STRATEGIES.get(args.strategy)
    if not strategy_cls:
        logger.error(f"Unknown strategy: {args.strategy}. Available: {list(STRATEGIES.keys())}")
        sys.exit(1)
    strategy = strategy_cls()

    # Build backtester
    bt = Backtester(
        symbol=args.symbol,
        strategy=strategy,
        initial_capital=args.capital,
    )

    days = args.days or 30

    # Ensure data exists
    candles = get_candles(limit=10, symbol=args.symbol)
    if not candles:
        logger.info(f"No data for {args.symbol}. Fetching from Yahoo Finance...")
        from data_client import MarketDataClient
        client = MarketDataClient(symbol=args.symbol)
        backfill_candles(client, days=days, symbol=args.symbol)
        compute_and_store_signal(client=client, symbol=args.symbol)

    # Run backtest
    if args.mode == "fast":
        # For fast mode, ensure signals exist
        from database import get_signals
        signals = get_signals(limit=5, symbol=args.symbol)
        if not signals:
            logger.info(f"No pre-computed signals for {args.symbol}. Computing initial signal...")
            from data_client import MarketDataClient
            client = MarketDataClient(symbol=args.symbol)
            compute_and_store_signal(client=client, symbol=args.symbol)
        result = bt.run_fast(days=days)
    else:
        result = bt.run_full(days=days, signal_step=args.step)

    # Output
    print_report(result, verbose=args.verbose)

    if args.json_output:
        to_json_file(result, args.json_output)
        logger.info(f"Results written to {args.json_output}")

    # Cache result in DB
    try:
        from database import insert_backtest_result
        from backtest_report import to_json
        import json as _json
        insert_backtest_result(
            symbol=args.symbol,
            strategy=args.strategy,
            mode=args.mode,
            params=strategy.get_params(),
            result_json=_json.dumps(to_json(result), default=str),
            computation_time=result.computation_time_seconds,
        )
    except Exception as e:
        logger.debug(f"Failed to cache backtest result: {e}")


def main():
    parser = argparse.ArgumentParser(description="FractalEdge")
    parser.add_argument(
        "command",
        choices=["demo", "live", "backfill", "backtest"],
        help="demo | live | backfill | backtest",
    )
    parser.add_argument("--days", type=int, default=None, help="Days to backfill/backtest")
    # Backtest arguments
    parser.add_argument("--strategy", type=str, default="regime",
                        choices=["regime", "breakout", "trend", "mean_reversion"],
                        help="Backtest strategy (default: regime)")
    parser.add_argument("--symbol", type=str, default="^NSEI",
                        help="Stock symbol to backtest (default: ^NSEI)")
    parser.add_argument("--mode", type=str, default="fast",
                        choices=["fast", "full"],
                        help="fast = use DB signals, full = recompute (slower)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Initial capital (default: 100000)")
    parser.add_argument("--step", type=int, default=5,
                        help="Signal step for full mode — compute every N bars (default: 5)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full trade log in backtest output")
    parser.add_argument("--json-output", type=str, default=None,
                        help="Write backtest results to JSON file")

    args = parser.parse_args()
    if args.days:
        config.backfill_days = args.days

    commands = {
        "demo": run_demo,
        "live": run_live,
        "backfill": run_backfill,
        "backtest": lambda: run_backtest(args),
    }
    commands[args.command]()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
