"""
FastAPI Backend — serves candles, signals, and health data to the dashboard.
Enhanced with multi-stock support, Angel One SmartAPI, and on-demand analysis.
"""
import os
import json
import logging
import asyncio
import threading
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query, HTTPException, Body, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from pathlib import Path
from fastapi.responses import FileResponse

from database import (
    get_candles, get_signals, get_latest_signal,
    get_latest_candle_timestamp, get_analyzed_symbols,
)
from data_client import get_stock_list, search_stocks, ALL_STOCKS

logger = logging.getLogger(__name__)

# ── Authentication ────────────────────────────────────────────────
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def require_auth(authorization: str = Header(None)):
    """Simple token-based auth for write endpoints. Skipped if ADMIN_TOKEN not set."""
    if not ADMIN_TOKEN:
        return  # No auth configured (local dev), allow all
    if not authorization or authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized. Login required to perform this action.")

# Track which symbol is currently being analyzed (for progress feedback)
_analysis_status = {}

# Current data source: "yahoo" or "angelone"
_current_data_source = "yahoo"

app = FastAPI(
    title="Fractal Stock Analyzer API",
    description="MFDFA/MFDCCA-powered fractal breakout detection with multi-stock support",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dashboard ──────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
def dashboard():
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return JSONResponse({"message": "dashboard.html not found."}, status_code=404)


# ── Stock Selection ────────────────────────────────────────────

@app.get("/stocks")
def stocks():
    """Return categorized stock lists for the dropdown."""
    return get_stock_list()


@app.get("/stocks/search")
def stock_search(q: str = Query(..., min_length=1, description="Search query")):
    """Search stocks by symbol or name."""
    return {"results": search_stocks(q)}


@app.get("/stocks/analyzed")
def analyzed_stocks():
    """Return symbols that have already been analyzed (have signals in DB)."""
    syms = get_analyzed_symbols()
    return {
        "symbols": [
            {"symbol": s, "name": ALL_STOCKS.get(s, s)} for s in syms
        ]
    }


def _compute_full_signal(all_candles, close_prices, secondary_prices, window, timestamp):
    """Compute a full signal with all analysis engines for a given window of data."""
    import numpy as np
    from config import config
    from feature_engine import run_feature_engine
    from trend_engine import compute_trend
    from sr_engine import compute_support_resistance, validate_sr_with_fractal
    from breakout_engine import detect_breakout, compute_breakout_quality
    from risk_engine import compute_risk_levels
    from mfdfa_engine import compute_mfdfa, compute_mfdcca, compute_scale_analysis
    from topless_engine import detect_topless_target

    sec_window = None
    if secondary_prices is not None and len(secondary_prices) >= len(close_prices):
        sec_window = secondary_prices[-len(close_prices):]
    elif secondary_prices is not None and len(secondary_prices) >= config.mfdfa.min_bars:
        sec_window = secondary_prices

    signal = run_feature_engine(close_prices, secondary_prices=sec_window, timestamp=timestamp)
    features = json.loads(signal.get("features_json", "{}"))

    mfdfa_result = compute_mfdfa(close_prices)
    scale_analysis = compute_scale_analysis(mfdfa_result)
    features["scale_analysis"] = scale_analysis
    features["alpha"] = mfdfa_result.get("alpha", [])
    features["f_alpha"] = mfdfa_result.get("f_alpha", [])
    features["tau_q"] = {str(q): round(v, 4) for q, v in mfdfa_result.get("tau_q", {}).items()}
    features["scales"] = mfdfa_result.get("scales", [])

    mfdcca_result = None
    if sec_window is not None and len(sec_window) >= config.mfdfa.min_bars:
        mfdcca_result = compute_mfdcca(close_prices, sec_window)
        features["rho_q"] = {str(q): v for q, v in mfdcca_result.get("rho_q", {}).items()}
        features["rho_s"] = {str(s): v for s, v in mfdcca_result.get("rho_s", {}).items()}
        features["hq_x"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_x", {}).items() if not np.isnan(v)}
        features["hq_y"] = {str(q): round(v, 4) for q, v in mfdcca_result.get("hq_y", {}).items() if not np.isnan(v)}
        features["alpha_xy"] = mfdcca_result.get("alpha_xy", [])
        features["f_alpha_xy"] = mfdcca_result.get("f_alpha_xy", [])
        features["spectral_width_xy"] = mfdcca_result.get("spectral_width_xy", 0.0)

    trend_data = compute_trend(all_candles)
    sr_data = compute_support_resistance(all_candles)
    sr_data = validate_sr_with_fractal(sr_data, scale_analysis, mfdfa_result)

    breakout_data = detect_breakout(
        all_candles, sr_data, trend_data,
        regime_label=signal.get("regime_label", "GREEN"),
    )
    breakout_quality = compute_breakout_quality(
        breakout_data, mfdfa_result,
        mfdcca_result=mfdcca_result, sr_data=sr_data, scale_analysis=scale_analysis,
    )
    risk_data = compute_risk_levels(
        current_price=close_prices[-1], atr=trend_data.get("atr_14", 0),
        sr_data=sr_data, trend_data=trend_data,
        regime_label=signal.get("regime_label", "GREEN"),
    )
    topless_data = detect_topless_target(
        all_candles, sr_data, mfdfa_result, breakout_data,
        mfdcca_result=mfdcca_result, scale_analysis=scale_analysis,
    )

    features["trend"] = trend_data
    features["support_resistance"] = sr_data
    features["breakout"] = breakout_data
    features["breakout_quality"] = breakout_quality
    features["risk"] = risk_data
    features["topless_target"] = topless_data
    signal["features_json"] = json.dumps(features)

    return signal


@app.post("/analyze/{symbol:path}")
def analyze_stock(symbol: str, source: str = Query(None), _=Depends(require_auth)):
    """
    Fetch data and run the full fractal analysis pipeline for the given stock symbol.
    Computes multiple historical signals over a sliding window so that
    stress/coupling/ensemble graphs have enough data points to render.
    """
    if symbol in _analysis_status and _analysis_status[symbol] == "running":
        return {"status": "already_running", "symbol": symbol, "message": "Analysis already in progress"}

    _analysis_status[symbol] = "running"
    try:
        from data_client import MarketDataClient
        from database import init_db, upsert_candles, insert_signal
        from config import config
        import numpy as np

        init_db()

        # Determine data source
        use_source = source or _current_data_source

        # Step 1: Fetch candles from selected data source
        if use_source == "angelone":
            from angelone_client import AngelOneDataClient, get_api_client, is_angel_symbol, is_logged_in
            if not is_logged_in():
                _analysis_status[symbol] = "error"
                raise HTTPException(status_code=503, detail="Angel One not logged in. Click 'Connect' first.")
            if not is_angel_symbol(symbol):
                _analysis_status[symbol] = "error"
                raise HTTPException(status_code=400, detail=f"Symbol '{symbol}' not available on Angel One. Use Yahoo Finance instead.")
            client = AngelOneDataClient(symbol=symbol, api_client=get_api_client())
        else:
            client = MarketDataClient(symbol=symbol)

        candles = client.get_candles_history(days=30, interval="5m")

        if not candles:
            _analysis_status[symbol] = "error"
            source_name = "Angel One" if use_source == "angelone" else "Yahoo Finance"
            raise HTTPException(status_code=404, detail=f"No data found for {symbol} on {source_name}")

        upsert_candles(candles, symbol=symbol)
        logger.info(f"Analyze {symbol}: fetched {len(candles)} candles")

        # Step 2: Fetch secondary for MFDCCA
        sec_candles = client.get_secondary_candles()
        all_secondary_prices = None
        if sec_candles and len(sec_candles) >= config.mfdfa.min_bars:
            all_secondary_prices = np.array([c["close"] for c in sec_candles])

        # Step 3: Load all candles from DB and compute multiple historical signals
        from database import get_candles as db_get_candles
        all_candles = db_get_candles(limit=5000, symbol=symbol)

        if len(all_candles) < config.mfdfa.min_bars:
            _analysis_status[symbol] = "error"
            raise HTTPException(
                status_code=400,
                detail=f"Not enough data for {symbol}: got {len(all_candles)} bars, need {config.mfdfa.min_bars}"
            )

        all_close = np.array([c["close"] for c in all_candles])
        window = config.mfdfa.window_size
        total_bars = len(all_close)

        # Compute signals over sliding windows (step=50 bars = ~4 hours)
        # This generates enough data points for the graphs
        step = 50
        start_idx = min(window, total_bars)  # start from first full window or all data
        computed = 0
        last_signal = None

        for i in range(start_idx, total_bars + 1, step):
            window_close = all_close[max(0, i - window):i]
            candle_window = all_candles[max(0, i - window):i]
            ts = all_candles[i - 1]["timestamp"]

            # Secondary prices window
            sec_window = None
            if all_secondary_prices is not None:
                sec_end = min(i, len(all_secondary_prices))
                sec_start = max(0, sec_end - window)
                if sec_end > sec_start and (sec_end - sec_start) >= config.mfdfa.min_bars:
                    sec_window = all_secondary_prices[sec_start:sec_end]

            try:
                signal = _compute_full_signal(
                    candle_window, window_close, sec_window, window, ts
                )
                insert_signal(signal, symbol=symbol)
                computed += 1
                last_signal = signal
            except Exception as e:
                logger.warning(f"Analyze {symbol} window @{ts}: {e}")

        # Ensure the very last bar is always computed (even if not on step boundary)
        if total_bars > start_idx and (total_bars - start_idx) % step != 0:
            window_close = all_close[max(0, total_bars - window):total_bars]
            candle_window = all_candles[max(0, total_bars - window):total_bars]
            ts = all_candles[-1]["timestamp"]

            sec_window = None
            if all_secondary_prices is not None:
                sec_end = min(total_bars, len(all_secondary_prices))
                sec_start = max(0, sec_end - window)
                if sec_end > sec_start and (sec_end - sec_start) >= config.mfdfa.min_bars:
                    sec_window = all_secondary_prices[sec_start:sec_end]

            try:
                signal = _compute_full_signal(
                    candle_window, window_close, sec_window, window, ts
                )
                insert_signal(signal, symbol=symbol)
                computed += 1
                last_signal = signal
            except Exception as e:
                logger.warning(f"Analyze {symbol} final window: {e}")

        if not last_signal:
            _analysis_status[symbol] = "error"
            raise HTTPException(status_code=500, detail=f"Failed to compute any signals for {symbol}")

        # Get stock info
        info = client.get_stock_info()

        _analysis_status[symbol] = "done"
        logger.info(f"Analyze {symbol}: DONE - {computed} signals computed, latest={last_signal['regime_label']} (ensemble={last_signal['ensemble_score']:.3f})")

        return {
            "status": "ok",
            "symbol": symbol,
            "name": info.get("name", symbol),
            "info": info,
            "regime_label": last_signal["regime_label"],
            "ensemble_score": last_signal["ensemble_score"],
            "hurst": last_signal["hurst_exponent"],
            "spectral_width": last_signal["spectral_width"],
            "candles_count": len(all_candles),
            "signals_computed": computed,
        }

    except HTTPException:
        raise
    except Exception as e:
        _analysis_status[symbol] = "error"
        logger.error(f"Analyze {symbol} error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analyze/status/{symbol:path}")
def analyze_status(symbol: str):
    """Check status of an analysis."""
    return {"symbol": symbol, "status": _analysis_status.get(symbol, "not_started")}


# ── Auth Check ────────────────────────────────────────────────────

@app.get("/auth/check")
def auth_check(_=Depends(require_auth)):
    """Verify if the provided token is valid."""
    return {"authenticated": True}


@app.get("/auth/required")
def auth_required():
    """Check if authentication is required (ADMIN_TOKEN is set)."""
    return {"auth_required": bool(ADMIN_TOKEN)}


# ── Health ─────────────────────────────────────────────────────────

@app.get("/health")
def health():
    latest_ts = get_latest_candle_timestamp()
    latest_signal = get_latest_signal()
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "latest_candle": latest_ts,
        "latest_signal_at": latest_signal["timestamp"] if latest_signal else None,
        "current_regime": latest_signal["regime_label"] if latest_signal else None,
    }


# ── Candles ────────────────────────────────────────────────────────

@app.get("/candles")
def candles(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    symbol: str = Query("^NSEI"),
):
    data = get_candles(start=start, end=end, limit=limit, symbol=symbol)
    return {"count": len(data), "candles": data}


# ── Signals ────────────────────────────────────────────────────────

@app.get("/signals")
def signals(
    limit: int = Query(200, ge=1, le=2000),
    symbol: str = Query("^NSEI"),
):
    data = get_signals(limit=limit, symbol=symbol)
    for s in data:
        if s.get("features_json"):
            try:
                s["features"] = json.loads(s["features_json"])
            except (json.JSONDecodeError, TypeError):
                s["features"] = {}
            del s["features_json"]
    return {"count": len(data), "signals": data}


# ── Latest ─────────────────────────────────────────────────────────

@app.get("/latest")
def latest(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal:
        raise HTTPException(status_code=404, detail=f"No signals for {symbol}")

    if signal.get("features_json"):
        try:
            signal["features"] = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            signal["features"] = {}
        del signal["features_json"]

    regime = signal.get("regime_label", "UNKNOWN")
    color_map = {"GREEN": "#22c55e", "AMBER": "#f59e0b", "RED": "#ef4444"}
    return {**signal, "regime_color": color_map.get(regime, "#6b7280")}


# ── Spectrum ──────────────────────────────────────────────────

@app.get("/spectrum")
def spectrum(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal or not signal.get("features_json"):
        raise HTTPException(status_code=404, detail="No spectrum data")

    try:
        features = json.loads(signal["features_json"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=500, detail="Invalid features")

    return {
        "timestamp": signal["timestamp"],
        "spectral_width": signal.get("spectral_width", 0),
        "hurst": signal.get("hurst_exponent"),
        "hq": features.get("hq", {}),
        "alpha": features.get("alpha", []),
        "f_alpha": features.get("f_alpha", []),
        "tau_q": features.get("tau_q", {}),
        "scales": features.get("scales", []),
        "scale_analysis": features.get("scale_analysis", {}),
        "features": features,
    }


# ── MFDCCA ─────────────────────────────────────────────────────

@app.get("/mfdcca")
def mfdcca(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal:
        raise HTTPException(status_code=404, detail="No signals")

    features = {}
    if signal.get("features_json"):
        try:
            features = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "timestamp": signal["timestamp"],
        "coupling_score": signal.get("coupling_score", 0.0),
        "coupling_raw": features.get("coupling_raw", 0.0),
        "hq_xy": features.get("hq_xy", {}),
        "hq_x": features.get("hq_x", {}),
        "hq_y": features.get("hq_y", {}),
        "rho_q": features.get("rho_q", {}),
        "rho_s": features.get("rho_s", {}),
        "alpha_xy": features.get("alpha_xy", []),
        "f_alpha_xy": features.get("f_alpha_xy", []),
        "spectral_width_xy": features.get("spectral_width_xy", 0.0),
        "regime_label": signal.get("regime_label"),
    }


@app.get("/coupling_history")
def coupling_history(
    limit: int = Query(200, ge=1, le=2000),
    symbol: str = Query("^NSEI"),
):
    data = get_signals(limit=limit, symbol=symbol)
    result = []
    for s in data:
        entry = {
            "timestamp": s["timestamp"],
            "coupling_score": s.get("coupling_score", 0.0),
            "stress_score": s.get("stress_score", 0.0),
            "ensemble_score": s.get("ensemble_score", 0.0),
            "regime_label": s.get("regime_label"),
        }
        if s.get("features_json"):
            try:
                feats = json.loads(s["features_json"])
                entry["coupling_raw"] = feats.get("coupling_raw", 0.0)
            except (json.JSONDecodeError, TypeError):
                pass
        result.append(entry)
    return {"count": len(result), "history": result}


# ── Analysis ──────────────────────────────────────────────────

@app.get("/analysis")
def analysis(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal:
        raise HTTPException(status_code=404, detail="No signals")

    features = {}
    if signal.get("features_json"):
        try:
            features = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "trend": features.get("trend", {}),
        "support_resistance": features.get("support_resistance", {}),
        "breakout": features.get("breakout", {}),
        "breakout_quality": features.get("breakout_quality", {}),
        "risk": features.get("risk", {}),
        "topless_target": features.get("topless_target", {}),
        "scale_analysis": features.get("scale_analysis", {}),
        "regime_label": signal.get("regime_label"),
        "timestamp": signal.get("timestamp"),
    }


# ── Scanner ──────────────────────────────────────────────────

@app.get("/scanner")
def breakout_scanner(symbol: str = Query("^NSEI")):
    data = get_signals(limit=50, symbol=symbol)
    scanner_results = []
    for s in data:
        features = {}
        if s.get("features_json"):
            try:
                features = json.loads(s["features_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        breakout = features.get("breakout", {})
        bq = features.get("breakout_quality", {})
        topless = features.get("topless_target", {})
        trend = features.get("trend", {})
        sr = features.get("support_resistance", {})

        scanner_results.append({
            "timestamp": s["timestamp"],
            "regime_label": s.get("regime_label"),
            "hurst": s.get("hurst_exponent"),
            "spectral_width": s.get("spectral_width"),
            "coupling_score": s.get("coupling_score", 0.0),
            "breakout_detected": breakout.get("breakout_detected", False),
            "direction": breakout.get("direction"),
            "quality_score": bq.get("quality_score", 0.0),
            "quality_signal": bq.get("quality_signal", "N/A"),
            "quality_components": bq.get("quality_components", {}),
            "is_topless": topless.get("is_topless", False),
            "topless_score": topless.get("topless_score", 0.0),
            "trend": trend.get("trend", "SIDEWAYS"),
            "rsi": trend.get("rsi_14", 50),
            "nearest_support": sr.get("nearest_support", 0),
            "nearest_resistance": sr.get("nearest_resistance", 0),
        })

    return {"count": len(scanner_results), "scanner": scanner_results}


# ── Topless ──────────────────────────────────────────────────

@app.get("/topless")
def topless_targets(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal:
        raise HTTPException(status_code=404, detail="No signals")

    features = {}
    if signal.get("features_json"):
        try:
            features = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "timestamp": signal["timestamp"],
        "regime_label": signal.get("regime_label"),
        "topless_target": features.get("topless_target", {}),
        "breakout_quality": features.get("breakout_quality", {}),
        "support_resistance": features.get("support_resistance", {}),
    }


# ── AI Endpoints ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: list = []
    symbol: str = "^NSEI"


@app.get("/ai/status")
def ai_status():
    from ai_engine import is_configured
    return {"configured": is_configured()}


@app.post("/ai/insight")
def ai_insight(symbol: str = Query("^NSEI"), _=Depends(require_auth)):
    from ai_engine import generate_insight, is_configured
    if not is_configured():
        raise HTTPException(status_code=503, detail="AI not configured. Set ANTHROPIC_API_KEY environment variable.")
    return generate_insight(symbol=symbol)


@app.post("/ai/chat")
def ai_chat(req: ChatRequest, _=Depends(require_auth)):
    from ai_engine import chat, is_configured
    if not is_configured():
        raise HTTPException(status_code=503, detail="AI not configured. Set ANTHROPIC_API_KEY environment variable.")
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    return chat(req.message, req.history, symbol=req.symbol)


# ── AI Trade Plan ─────────────────────────────────────────────

@app.post("/ai/trade-plan")
def ai_trade_plan(symbol: str = Query("^NSEI"), _=Depends(require_auth)):
    from ai_engine import generate_trade_plan, is_configured
    if not is_configured():
        raise HTTPException(status_code=503, detail="AI not configured. Set ANTHROPIC_API_KEY environment variable.")
    return generate_trade_plan(symbol=symbol)


# ── Regime Transition ─────────────────────────────────────────

@app.get("/regime/transition")
def regime_transition(symbol: str = Query("^NSEI")):
    from regime_engine import compute_transition_matrix
    return compute_transition_matrix(symbol=symbol)


# ── Health Score ──────────────────────────────────────────────

@app.get("/health-score")
def health_score(symbol: str = Query("^NSEI")):
    from health_score_engine import compute_health_score
    return compute_health_score(symbol=symbol)


# ── Coupling Divergence ──────────────────────────────────────

@app.get("/coupling/divergence")
def coupling_divergence(symbol: str = Query("^NSEI")):
    from feature_engine import detect_coupling_divergence
    return detect_coupling_divergence(symbol=symbol)


# ── Market Memory ─────────────────────────────────────────────

@app.get("/market-memory")
def market_memory(symbol: str = Query("^NSEI")):
    signal = get_latest_signal(symbol=symbol)
    if not signal or not signal.get("features_json"):
        raise HTTPException(status_code=404, detail="No analysis data")
    try:
        features = json.loads(signal["features_json"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=500, detail="Invalid features")
    mm = features.get("market_memory", {})
    if not mm:
        raise HTTPException(status_code=404, detail="No market memory data. Re-analyze the stock.")
    return mm


# ── Alerts ────────────────────────────────────────────────────

@app.get("/alerts")
def alerts_list(symbol: str = Query(None), unread: bool = Query(False), limit: int = Query(50)):
    from database import get_alerts
    return {"alerts": get_alerts(symbol=symbol, unread_only=unread, limit=limit)}


@app.get("/alerts/count")
def alert_count():
    from database import get_unread_alert_count
    return {"unread": get_unread_alert_count()}


@app.post("/alerts/read/{alert_id}")
def read_alert(alert_id: int):
    from database import mark_alert_read
    mark_alert_read(alert_id)
    return {"status": "ok"}


@app.post("/alerts/read-all")
def read_all_alerts(symbol: str = Query(None)):
    from database import mark_all_alerts_read
    mark_all_alerts_read(symbol=symbol)
    return {"status": "ok"}


# ── Portfolio ─────────────────────────────────────────────────

class PortfolioCreate(BaseModel):
    name: str
    holdings: list = []


@app.post("/portfolio")
def create_portfolio_endpoint(req: PortfolioCreate, _=Depends(require_auth)):
    from database import create_portfolio
    pid = create_portfolio(req.name, req.holdings)
    return {"id": pid, "status": "created"}


@app.get("/portfolios")
def list_portfolios():
    from database import get_portfolios
    return {"portfolios": get_portfolios()}


@app.get("/portfolio/{portfolio_id}")
def get_portfolio_detail(portfolio_id: int):
    from database import get_portfolio
    p = get_portfolio(portfolio_id)
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return p


@app.get("/portfolio/{portfolio_id}/stress-test")
def portfolio_stress_test(portfolio_id: int):
    from portfolio_engine import compute_portfolio_stress
    return compute_portfolio_stress(portfolio_id)


@app.delete("/portfolio/{portfolio_id}")
def delete_portfolio_endpoint(portfolio_id: int, _=Depends(require_auth)):
    from database import delete_portfolio
    delete_portfolio(portfolio_id)
    return {"status": "deleted"}


# ── Position Size Calculator ──────────────────────────────────

class PositionSizeRequest(BaseModel):
    account_size: float
    risk_pct: float
    entry_price: float
    stoploss: float


@app.post("/calculator/position-size")
def position_size_calc(req: PositionSizeRequest):
    from portfolio_engine import compute_position_size
    return compute_position_size(req.account_size, req.risk_pct, req.entry_price, req.stoploss)


# ── Sector Rotation ───────────────────────────────────────────

@app.get("/sectors")
def sectors(period: str = Query("1mo")):
    from sector_engine import compute_sector_rotation
    return compute_sector_rotation(period=period)


# ── Economic Calendar ─────────────────────────────────────────

@app.get("/calendar")
def calendar(days: int = Query(30)):
    from calendar_engine import get_upcoming_events
    return get_upcoming_events(days_ahead=days)


# ── Watchlist Heatmap ─────────────────────────────────────────

class HeatmapRequest(BaseModel):
    symbols: list = []


@app.post("/watchlist/heatmap")
def watchlist_heatmap(req: HeatmapRequest):
    """Return latest regime/stress for multiple symbols."""
    results = []
    for sym in req.symbols[:20]:
        signal = get_latest_signal(symbol=sym)
        if signal:
            results.append({
                "symbol": sym,
                "name": ALL_STOCKS.get(sym, sym),
                "regime": signal.get("regime_label"),
                "stress": signal.get("stress_score"),
                "ensemble": signal.get("ensemble_score"),
                "hurst": signal.get("hurst_exponent"),
            })
    return {"count": len(results), "heatmap": results}


# ── News ──────────────────────────────────────────────────────

@app.get("/news")
def news(symbol: str = Query("^NSEI")):
    """Return combined stock + market news for the given symbol."""
    from news_engine import get_all_news
    return get_all_news(symbol=symbol)


@app.get("/news/categorized")
def news_categorized(symbol: str = Query(None)):
    """Return global news organized by categories (latest, stocks, crypto, etc.).
    Symbol is optional — if provided, stock-specific news is also included."""
    from news_engine import get_categorized_news
    return get_categorized_news(symbol=symbol)


@app.get("/news/market")
def market_news():
    """Return general market news only (no symbol needed)."""
    from news_engine import get_market_news_only
    return get_market_news_only()


# ── Data Source Toggle ────────────────────────────────────────────

@app.get("/datasource")
def get_datasource():
    """Return current data source and Angel One status."""
    from angelone_client import get_status
    return {
        "source": _current_data_source,
        "angelone": get_status(),
    }


@app.post("/datasource")
def set_datasource(source: str = Body(..., embed=True)):
    """Switch between 'yahoo' and 'angelone' data source."""
    global _current_data_source
    if source not in ("yahoo", "angelone"):
        raise HTTPException(status_code=400, detail="Invalid source. Use 'yahoo' or 'angelone'.")

    if source == "angelone":
        from angelone_client import is_logged_in
        from config import config
        if not config.smartapi.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Angel One not configured. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET environment variables."
            )

    _current_data_source = source
    logger.info(f"Data source switched to: {source}")
    return {"status": "ok", "source": source}


# ── Angel One SmartAPI ────────────────────────────────────────────

@app.get("/angelone/status")
def angelone_status():
    """Check Angel One SmartAPI configuration and login status."""
    from angelone_client import get_status
    return get_status()


@app.post("/angelone/login")
def angelone_login():
    """Login to Angel One SmartAPI (generates TOTP automatically)."""
    from config import config
    if not config.smartapi.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Angel One not configured. Set environment variables: "
                   "ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET"
        )

    from angelone_client import login
    result = login()
    if result["status"] == "ok":
        return result
    else:
        raise HTTPException(status_code=401, detail=result["message"])


@app.get("/angelone/ltp")
def angelone_ltp(symbol: str = Query("^NSEI")):
    """Get real-time Last Traded Price from Angel One."""
    from angelone_client import is_logged_in, get_api_client, get_angel_token, get_ws_manager
    if not is_logged_in():
        raise HTTPException(status_code=503, detail="Angel One not logged in.")

    # Try WebSocket first (fastest)
    ws = get_ws_manager()
    tick = ws.get_ltp(symbol)
    if tick and tick.get("ltp", 0) > 0:
        return {"symbol": symbol, "source": "websocket", **tick}

    # Fallback to REST LTP
    token_info = get_angel_token(symbol)
    if not token_info:
        raise HTTPException(status_code=400, detail=f"Symbol '{symbol}' not available on Angel One.")

    client = get_api_client()
    ltp_data = client.get_ltp(token_info["exchange"], token_info["token"])
    if ltp_data:
        return {"symbol": symbol, "source": "rest", "ltp": ltp_data.get("ltp", 0), **ltp_data}
    else:
        raise HTTPException(status_code=404, detail=f"No LTP data for {symbol}")


@app.post("/angelone/websocket/start")
def angelone_ws_start(symbol: str = Query("^NSEI")):
    """Start WebSocket streaming for a symbol."""
    from angelone_client import is_logged_in, get_ws_manager

    if not is_logged_in():
        raise HTTPException(status_code=503, detail="Angel One not logged in.")

    ws = get_ws_manager()
    ws.subscribe(symbol)

    if not ws._running:
        ws.start()

    return {"status": "ok", "symbol": symbol, "streaming": True}


@app.post("/angelone/websocket/stop")
def angelone_ws_stop():
    """Stop WebSocket streaming."""
    from angelone_client import get_ws_manager
    ws = get_ws_manager()
    ws.stop()
    return {"status": "ok", "streaming": False}


@app.get("/stream/ltp")
async def stream_ltp(symbol: str = Query("^NSEI")):
    """Server-Sent Events stream for live LTP updates."""
    from angelone_client import is_logged_in, get_ws_manager

    if not is_logged_in():
        raise HTTPException(status_code=503, detail="Angel One not logged in.")

    async def event_generator():
        ws = get_ws_manager()
        last_ltp = 0

        while True:
            tick = ws.get_ltp(symbol)
            if tick and tick.get("ltp", 0) != last_ltp:
                last_ltp = tick.get("ltp", 0)
                data = json.dumps({
                    "symbol": symbol,
                    "ltp": last_ltp,
                    "timestamp": tick.get("timestamp", ""),
                    "volume": tick.get("volume", 0),
                    "change": tick.get("change", 0),
                })
                yield f"data: {data}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Push Notifications ───────────────────────────────────────────

@app.get("/notifications/status")
def notification_status():
    """Return current notification configuration status."""
    from config import config
    nc = config.notifications
    return {
        "telegram": {
            "enabled": nc.telegram.enabled,
            "configured": nc.telegram.is_configured(),
        },
        "email": {
            "enabled": nc.email.enabled,
            "configured": nc.email.is_configured(),
        },
        "min_severity": nc.min_severity,
        "cooldown_seconds": nc.cooldown_seconds,
        "quiet_hours": f"{nc.quiet_hours_start}:00 - {nc.quiet_hours_end}:00",
    }


@app.post("/notifications/test")
def test_notification(channel: str = Query("all")):
    """Send a test notification to verify Telegram/Email configuration."""
    from notification_engine import send_test_notification
    return send_test_notification(channel)


@app.get("/notifications/log")
def notification_log(limit: int = Query(50)):
    """Fetch recent notification send log."""
    from database import get_notification_log
    return {"log": get_notification_log(limit=limit)}


@app.post("/notifications/toggle")
def toggle_notifications(channel: str = Body(...), enabled: bool = Body(...)):
    """Enable or disable a notification channel at runtime."""
    from config import config
    nc = config.notifications
    if channel == "telegram":
        nc.telegram.enabled = enabled
    elif channel == "email":
        nc.email.enabled = enabled
    else:
        raise HTTPException(status_code=400, detail="Channel must be 'telegram' or 'email'")
    return {"status": "ok", "channel": channel, "enabled": enabled}


@app.post("/notifications/settings")
def update_notification_settings(
    min_severity: str = Body(None),
    cooldown_seconds: int = Body(None),
):
    """Update notification filtering settings at runtime."""
    from config import config
    nc = config.notifications
    updated = {}
    if min_severity and min_severity in ("info", "warning", "critical"):
        nc.min_severity = min_severity
        updated["min_severity"] = min_severity
    if cooldown_seconds is not None and cooldown_seconds >= 0:
        nc.cooldown_seconds = cooldown_seconds
        updated["cooldown_seconds"] = cooldown_seconds
    if not updated:
        raise HTTPException(status_code=400, detail="No valid settings provided")
    return {"status": "ok", "updated": updated}


# ── Backtesting ──────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    symbol: str = "^NSEI"
    strategy: str = "regime"
    mode: str = "fast"
    days: int = 30
    initial_capital: float = 100000.0
    signal_step: int = 5
    strategy_params: dict = {}


@app.post("/backtest")
def run_backtest_api(req: BacktestRequest, _=Depends(require_auth)):
    """Run a backtest on-demand. Fast mode uses pre-computed signals; full mode recomputes."""
    from backtest_strategies import STRATEGIES
    from backtest_engine import Backtester
    from backtest_report import to_json

    strategy_cls = STRATEGIES.get(req.strategy)
    if not strategy_cls:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy: {req.strategy}. Available: {list(STRATEGIES.keys())}",
        )

    try:
        strategy = strategy_cls(**req.strategy_params)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid strategy params: {e}")

    bt = Backtester(
        symbol=req.symbol,
        strategy=strategy,
        initial_capital=req.initial_capital,
    )

    if req.mode == "fast":
        result = bt.run_fast(days=req.days)
    else:
        result = bt.run_full(days=req.days, signal_step=req.signal_step)

    # Cache result
    try:
        from database import insert_backtest_result
        result_data = to_json(result)
        insert_backtest_result(
            symbol=req.symbol,
            strategy=req.strategy,
            mode=req.mode,
            params=strategy.get_params(),
            result_json=json.dumps(result_data, default=str),
            computation_time=result.computation_time_seconds,
        )
    except Exception:
        pass

    return to_json(result)


@app.get("/backtest/strategies")
def list_strategies():
    """Return available backtest strategies with descriptions and default params."""
    from backtest_strategies import STRATEGIES
    return {
        "strategies": [
            {
                "id": name,
                "name": cls().get_name(),
                "description": cls().get_description(),
                "params": cls().get_params(),
            }
            for name, cls in STRATEGIES.items()
        ]
    }


@app.get("/backtest/results")
def backtest_results(
    symbol: str = Query(None),
    strategy: str = Query(None),
    limit: int = Query(20),
):
    """Fetch cached backtest results (summary, without full trade log)."""
    from database import get_backtest_results
    return {"results": get_backtest_results(symbol=symbol, strategy=strategy, limit=limit)}


@app.get("/backtest/results/{result_id}")
def backtest_result_detail(result_id: int):
    """Fetch a single backtest result with full trade log and equity curve."""
    from database import get_backtest_result
    r = get_backtest_result(result_id)
    if not r:
        raise HTTPException(status_code=404, detail="Backtest result not found")
    return r
