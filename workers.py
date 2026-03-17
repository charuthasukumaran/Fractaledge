"""
Workers — Backfill historical data & run periodic signal updates.
Enhanced with fractal S/R validation, breakout quality scoring, and topless target detection.
Supports any stock symbol (multi-stock).
"""
import time
import json
import logging
import numpy as np
from datetime import datetime

from config import config
from database import (
    init_db, upsert_candles, get_candles, get_latest_candle_timestamp,
    insert_signal, log_health,
)
from data_client import MarketDataClient
from feature_engine import run_feature_engine
from trend_engine import compute_trend
from sr_engine import compute_support_resistance, validate_sr_with_fractal
from breakout_engine import detect_breakout, compute_breakout_quality
from risk_engine import compute_risk_levels
from mfdfa_engine import compute_mfdfa, compute_mfdcca, compute_scale_analysis
from topless_engine import detect_topless_target

logger = logging.getLogger(__name__)


def backfill_candles(client: MarketDataClient, days: int = None, symbol: str = "^NSEI"):
    """Backfill historical 5-min candles from Yahoo Finance."""
    days = days or config.backfill_days
    logger.info(f"Backfilling {days} days of {symbol} 5-min candles from Yahoo Finance...")

    candles = client.get_candles_history(days=days, interval="5m")

    if candles:
        inserted = upsert_candles(candles, symbol=symbol)
        logger.info(f"Backfill complete ({symbol}): {len(candles)} fetched, {inserted} new rows inserted")
        log_health("backfill", "ok", f"{inserted} candles inserted for {symbol}")
    else:
        logger.warning(f"Backfill returned zero candles for {symbol}")
        log_health("backfill", "warning", f"Zero candles returned for {symbol}")

    return candles


def update_latest_candles(client: MarketDataClient, symbol: str = "^NSEI"):
    """Fetch the most recent candles."""
    logger.info(f"Fetching latest candles for {symbol}...")
    candles = client.get_latest_candles()

    if candles:
        inserted = upsert_candles(candles, symbol=symbol)
        logger.info(f"Update ({symbol}): {len(candles)} fetched, {inserted} new")
    else:
        logger.debug(f"No new candles available for {symbol}")


def compute_and_store_signal(client: MarketDataClient = None, secondary_prices_override: np.ndarray = None, symbol: str = "^NSEI"):
    """Fetch latest candle window, run full fractal analysis pipeline, store signal."""
    window = config.mfdfa.window_size
    candles = get_candles(limit=window, symbol=symbol)

    if len(candles) < config.mfdfa.min_bars:
        logger.warning(f"Not enough candles for signal ({symbol}): {len(candles)} < {config.mfdfa.min_bars}")
        return None

    close_prices = np.array([c["close"] for c in candles])
    latest_ts = candles[-1]["timestamp"]

    # Fetch secondary series for MFDCCA coupling analysis
    secondary_prices = secondary_prices_override
    if secondary_prices is None and client is not None:
        try:
            sec_candles = client.get_secondary_candles()
            if sec_candles and len(sec_candles) >= config.mfdfa.min_bars:
                secondary_prices = np.array([c["close"] for c in sec_candles[-window:]])
                logger.info(f"MFDCCA ({symbol}): using {len(secondary_prices)} secondary bars for coupling")
            else:
                logger.warning(f"MFDCCA ({symbol}): insufficient secondary data ({len(sec_candles) if sec_candles else 0} bars)")
        except Exception as e:
            logger.error(f"MFDCCA secondary fetch failed ({symbol}): {e}")

    # Step 1: Base signal (MFDFA + MFDCCA stress/coupling scores)
    signal = run_feature_engine(close_prices, secondary_prices=secondary_prices, timestamp=latest_ts)

    # Step 2: Full fractal analysis pipeline
    try:
        features = json.loads(signal.get("features_json", "{}"))

        # Raw MFDFA result for downstream engines
        mfdfa_result = compute_mfdfa(close_prices)

        # Scale analysis (dominant timescale, local Hursts)
        scale_analysis = compute_scale_analysis(mfdfa_result)
        features["scale_analysis"] = scale_analysis
        features["alpha"] = mfdfa_result.get("alpha", [])
        features["f_alpha"] = mfdfa_result.get("f_alpha", [])
        features["tau_q"] = {str(q): round(v, 4) for q, v in mfdfa_result.get("tau_q", {}).items()}
        features["scales"] = mfdfa_result.get("scales", [])

        # MFDCCA detailed results
        mfdcca_result = None
        if secondary_prices is not None and len(secondary_prices) >= config.mfdfa.min_bars:
            mfdcca_result = compute_mfdcca(close_prices, secondary_prices)
            features["rho_q"] = {str(q): v for q, v in mfdcca_result.get("rho_q", {}).items()}
            features["rho_s"] = {str(s): v for s, v in mfdcca_result.get("rho_s", {}).items()}
            features["hq_x"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_x", {}).items() if not np.isnan(v)}
            features["hq_y"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_y", {}).items() if not np.isnan(v)}
            features["alpha_xy"] = mfdcca_result.get("alpha_xy", [])
            features["f_alpha_xy"] = mfdcca_result.get("f_alpha_xy", [])
            features["spectral_width_xy"] = mfdcca_result.get("spectral_width_xy", 0.0)

        # Trend engine
        trend_data = compute_trend(candles)

        # Support/Resistance with fractal validation
        sr_data = compute_support_resistance(candles)
        sr_data = validate_sr_with_fractal(sr_data, scale_analysis, mfdfa_result)

        # Breakout detection
        breakout_data = detect_breakout(
            candles, sr_data, trend_data,
            regime_label=signal.get("regime_label", "GREEN"),
        )

        # Breakout quality scoring (fractal-aware)
        breakout_quality = compute_breakout_quality(
            breakout_data, mfdfa_result,
            mfdcca_result=mfdcca_result,
            sr_data=sr_data,
            scale_analysis=scale_analysis,
        )

        # Risk engine
        risk_data = compute_risk_levels(
            current_price=close_prices[-1],
            atr=trend_data.get("atr_14", 0),
            sr_data=sr_data,
            trend_data=trend_data,
            regime_label=signal.get("regime_label", "GREEN"),
        )

        # Topless target detection
        topless_data = detect_topless_target(
            candles, sr_data, mfdfa_result, breakout_data,
            mfdcca_result=mfdcca_result,
            scale_analysis=scale_analysis,
        )

        # Market memory (from scale analysis)
        from mfdfa_engine import compute_market_memory
        market_memory = compute_market_memory(scale_analysis)

        # Coupling divergence
        from feature_engine import detect_coupling_divergence
        coupling_div = detect_coupling_divergence(symbol=symbol)

        # Store all results
        features["trend"] = trend_data
        features["support_resistance"] = sr_data
        features["breakout"] = breakout_data
        features["breakout_quality"] = breakout_quality
        features["risk"] = risk_data
        features["topless_target"] = topless_data
        features["market_memory"] = market_memory
        features["coupling_divergence"] = coupling_div
        signal["features_json"] = json.dumps(features)

        logger.info(
            f"   [{symbol}] trend={trend_data['trend']} RSI={trend_data['rsi_14']} "
            f"S={sr_data['nearest_support']} R={sr_data['nearest_resistance']} "
            f"breakout={'YES' if breakout_data['breakout_detected'] else 'no'} "
            f"quality={breakout_quality.get('quality_score', 0):.2f} "
            f"topless={'YES' if topless_data.get('is_topless') else 'no'} "
            f"memory={market_memory.get('memory_time_str', '?')}"
        )
    except Exception as e:
        logger.error(f"Analysis engines error ({symbol}): {e}", exc_info=True)

    insert_signal(signal, symbol=symbol)

    # Check alerts after signal is stored
    try:
        from alert_engine import check_alerts
        triggered = check_alerts(symbol=symbol)
        if triggered:
            logger.info(f"   [{symbol}] {len(triggered)} alert(s) triggered")
            # Dispatch push notifications (Telegram + Email)
            try:
                from notification_engine import dispatch_alerts
                send_results = dispatch_alerts(triggered, symbol)
                for r in send_results:
                    if r.get("telegram") or r.get("email"):
                        channels = [k for k in ("telegram", "email") if r.get(k)]
                        logger.info(f"   [{symbol}] Notification sent via {', '.join(channels)}: {r['alert_type']}")
            except Exception as ne:
                logger.error(f"Notification dispatch error ({symbol}): {ne}")
    except Exception as e:
        logger.error(f"Alert engine error ({symbol}): {e}")

    log_health("signal_engine", "ok", f"[{symbol}] regime={signal['regime_label']} coupling={signal['coupling_score']:.3f}")
    return signal


def _self_ping():
    """Ping own /health endpoint to prevent Render from spinning down."""
    import os
    import urllib.request
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if url:
        try:
            urllib.request.urlopen(f"{url}/health", timeout=10)
        except Exception:
            pass


def run_worker_loop(client: MarketDataClient, symbol: str = "^NSEI"):
    """Main worker loop — fetches candles and computes signals every 5 minutes."""
    from database import prune_old_data

    interval = config.candle_interval_minutes * 60

    logger.info(f"Starting worker loop for {symbol} (every {config.candle_interval_minutes} min)")
    logger.info("During market hours, new candles will appear.")
    logger.info("Outside market hours, it will keep running but no new data arrives.\n")

    while True:
        try:
            update_latest_candles(client, symbol=symbol)
            signal = compute_and_store_signal(client=client, symbol=symbol)
            if signal:
                logger.info(
                    f">> [{symbol}] {signal['regime_label']} | "
                    f"ensemble={signal['ensemble_score']:.3f} | "
                    f"stress={signal['stress_score']:.3f} | "
                    f"delta_alpha={signal['spectral_width']:.4f}"
                )
        except Exception as e:
            logger.error(f"Worker cycle error ({symbol}): {e}")
            log_health("worker", "error", f"[{symbol}] {str(e)}")

        # Prune old data to stay within free-tier storage limits
        try:
            prune_old_data(max_candle_days=14, max_signal_days=14)
        except Exception as e:
            logger.error(f"Pruning error: {e}")

        # Self-ping to prevent Render spin-down
        _self_ping()

        logger.info(f"[{symbol}] Sleeping {config.candle_interval_minutes} min until next update...\n")
        time.sleep(interval)
