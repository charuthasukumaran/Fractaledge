"""
Feature & Ensemble Engine
--------------------------
Takes raw candle data, computes MFDFA features, and produces:
  - stress_score [0, 1]
  - coupling_score [0, 1] (if secondary series provided)
  - ensemble_score [0, 1]
  - regime_label: GREEN / AMBER / RED
"""
import json
import logging
import numpy as np
from datetime import datetime
from typing import Optional

from mfdfa_engine import compute_mfdfa, compute_mfdcca
from config import config

logger = logging.getLogger(__name__)


def _normalize_score(value: float, low: float, high: float) -> float:
    """Clamp and normalize a value to [0, 1] given expected range."""
    if high <= low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def compute_stress_score(mfdfa_result: dict) -> tuple[float, dict]:
    """
    Convert MFDFA output into a single stress score [0, 1].
    
    Stress indicators (higher = more stressed):
      1. Spectral width (Δα): wider spectrum → more multifractality → stress
      2. Hurst deviation from 0.5: |h(2) - 0.5| → persistence/anti-persistence
      3. Asymmetry of h(q): difference between h(q<0) and h(q>0)
      4. Volatility of h(q) across q-orders
    
    Returns: (score, features_dict)
    """
    if "error" in mfdfa_result:
        return 0.5, {"error": mfdfa_result["error"]}

    hq = mfdfa_result["hq"]
    spectral_width = mfdfa_result["spectral_width"]
    hurst = mfdfa_result.get("hurst", 0.5)

    # Feature 1: Spectral width (typical range 0.1–0.8 for financial data)
    width_score = _normalize_score(spectral_width, 0.1, 0.8)

    # Feature 2: Hurst deviation from efficient market (h=0.5)
    hurst_dev = abs(hurst - 0.5) if not np.isnan(hurst) else 0.0
    hurst_score = _normalize_score(hurst_dev, 0.0, 0.3)

    # Feature 3: Asymmetry — difference between negative and positive q behavior
    neg_q_h = [hq[q] for q in hq if q < 0 and not np.isnan(hq[q])]
    pos_q_h = [hq[q] for q in hq if q > 0 and not np.isnan(hq[q])]
    if neg_q_h and pos_q_h:
        asymmetry = abs(np.mean(neg_q_h) - np.mean(pos_q_h))
    else:
        asymmetry = 0.0
    asym_score = _normalize_score(asymmetry, 0.0, 0.5)

    # Feature 4: Volatility of h(q) across q-orders
    valid_h = [v for v in hq.values() if not np.isnan(v)]
    hq_std = float(np.std(valid_h)) if len(valid_h) > 2 else 0.0
    vol_score = _normalize_score(hq_std, 0.0, 0.2)

    # Weighted ensemble of features
    weights = {
        "spectral_width": 0.35,
        "hurst_deviation": 0.25,
        "asymmetry": 0.20,
        "hq_volatility": 0.20,
    }
    stress = (
        weights["spectral_width"] * width_score
        + weights["hurst_deviation"] * hurst_score
        + weights["asymmetry"] * asym_score
        + weights["hq_volatility"] * vol_score
    )

    features = {
        "spectral_width_raw": spectral_width,
        "spectral_width_score": round(width_score, 4),
        "hurst": round(hurst, 4) if not np.isnan(hurst) else None,
        "hurst_deviation_score": round(hurst_score, 4),
        "asymmetry_raw": round(asymmetry, 4),
        "asymmetry_score": round(asym_score, 4),
        "hq_std_raw": round(hq_std, 4),
        "hq_volatility_score": round(vol_score, 4),
        "hq": {str(q): round(v, 4) for q, v in hq.items() if not np.isnan(v)},
    }

    return round(float(stress), 4), features


def compute_coupling_score(mfdcca_result: dict) -> float:
    """Convert MFDCCA output to a coupling score [0, 1]."""
    if "error" in mfdcca_result:
        return 0.0
    cs = mfdcca_result.get("coupling_strength", 0.0)
    return round(_normalize_score(cs, 0.0, 1.0), 4)


def classify_regime(ensemble_score: float) -> str:
    """Classify into GREEN / AMBER / RED based on ensemble score."""
    cfg = config.mfdfa
    if ensemble_score <= cfg.stress_green_max:
        return "GREEN"
    elif ensemble_score <= cfg.stress_amber_max:
        return "AMBER"
    else:
        return "RED"


def run_feature_engine(
    close_prices: np.ndarray,
    secondary_prices: Optional[np.ndarray] = None,
    timestamp: str = None,
) -> dict:
    """
    Main entry point: run the full feature + ensemble pipeline.
    
    Parameters:
        close_prices: array of NIFTY close prices (5-min bars)
        secondary_prices: optional second series for MFDCCA (e.g., BANKNIFTY)
        timestamp: timestamp of the latest candle
    
    Returns signal dict ready for database insertion.
    """
    timestamp = timestamp or datetime.utcnow().isoformat()

    # 1. MFDFA on primary series
    mfdfa_result = compute_mfdfa(close_prices)
    stress_score, features = compute_stress_score(mfdfa_result)

    # 2. Optional MFDCCA coupling
    coupling_score = 0.0
    if secondary_prices is not None and len(secondary_prices) >= config.mfdfa.min_bars:
        mfdcca_result = compute_mfdcca(close_prices, secondary_prices)
        coupling_score = compute_coupling_score(mfdcca_result)
        features["coupling_raw"] = mfdcca_result.get("coupling_strength", 0.0)
        # Store full MFDCCA h_xy(q) spectrum for dashboard visualization
        hq_xy = mfdcca_result.get("hq_xy", {})
        features["hq_xy"] = {str(q): round(v, 4) for q, v in hq_xy.items() if not np.isnan(v)}
        features["coupling_score"] = coupling_score

    # 3. Ensemble score (weighted combination)
    if coupling_score > 0:
        ensemble_score = round(0.7 * stress_score + 0.3 * coupling_score, 4)
    else:
        ensemble_score = stress_score

    # 4. Classify regime
    regime_label = classify_regime(ensemble_score)

    logger.info(
        f"Signal @ {timestamp}: stress={stress_score:.3f} coupling={coupling_score:.3f} "
        f"ensemble={ensemble_score:.3f} → {regime_label}"
    )

    return {
        "timestamp": timestamp,
        "stress_score": stress_score,
        "coupling_score": coupling_score,
        "ensemble_score": ensemble_score,
        "regime_label": regime_label,
        "hurst_exponent": features.get("hurst"),
        "spectral_width": features.get("spectral_width_raw", 0.0),
        "features_json": json.dumps(features),
        "computed_at": datetime.utcnow().isoformat(),
    }


def detect_coupling_divergence(symbol: str = "^NSEI", lookback: int = 10) -> dict:
    """
    Detect sharp drops in MFDCCA coupling over recent bars.
    A drop > 0.2 in 5 bars signals divergence.

    Returns:
        {
            "divergence_detected": bool,
            "magnitude": float,
            "direction": str,
            "alert_level": str,
            "coupling_history": list,
        }
    """
    from database import get_signals

    signals = get_signals(limit=lookback, symbol=symbol)
    coupling_history = [
        round(s.get("coupling_score", 0), 4) for s in signals
        if s.get("coupling_score") is not None
    ]

    if len(coupling_history) < 5:
        return {
            "divergence_detected": False,
            "magnitude": 0,
            "direction": "stable",
            "alert_level": "none",
            "coupling_history": coupling_history,
        }

    # Look at 5-bar change
    recent = coupling_history[-5:]
    change = recent[-1] - recent[0]
    magnitude = abs(change)

    if change < -0.2:
        direction = "decoupling"
        alert_level = "critical" if magnitude > 0.35 else "warning"
        divergence = True
    elif change > 0.2:
        direction = "recoupling"
        alert_level = "warning"
        divergence = True
    else:
        direction = "stable"
        alert_level = "none"
        divergence = False

    return {
        "divergence_detected": divergence,
        "magnitude": round(magnitude, 4),
        "direction": direction,
        "alert_level": alert_level,
        "coupling_history": coupling_history,
    }
