"""
Alert Engine — checks conditions after each signal computation and generates alerts.
"""
import json
import logging
from typing import Optional
from database import get_signals, insert_alert

logger = logging.getLogger(__name__)


def check_alerts(symbol: str = "^NSEI") -> list:
    """
    Run all alert checks for a symbol after a new signal is stored.
    Returns list of triggered alert dicts.
    """
    signals = get_signals(limit=10, symbol=symbol)
    if len(signals) < 2:
        return []

    triggered = []

    checks = [
        _check_regime_change,
        _check_stress_spike,
        _check_breakout,
        _check_coupling_divergence,
    ]

    for check_fn in checks:
        try:
            alert = check_fn(signals, symbol)
            if alert:
                insert_alert(
                    symbol=symbol,
                    alert_type=alert["type"],
                    message=alert["message"],
                    severity=alert["severity"],
                )
                triggered.append(alert)
        except Exception as e:
            logger.error(f"Alert check {check_fn.__name__} error: {e}")

    return triggered


def _check_regime_change(signals: list, symbol: str) -> Optional[dict]:
    """Alert if regime changed from last signal."""
    curr = signals[-1].get("regime_label")
    prev = signals[-2].get("regime_label")

    if curr and prev and curr != prev:
        severity = "critical" if curr == "RED" else "warning" if curr == "AMBER" else "info"
        direction = "worsened" if _regime_rank(curr) > _regime_rank(prev) else "improved"

        return {
            "type": "regime_change",
            "severity": severity,
            "message": f"Regime {direction}: {prev} -> {curr} for {symbol}. "
                       f"{'Exercise caution.' if curr == 'RED' else 'Market conditions changing.' if curr == 'AMBER' else 'Conditions improving.'}",
        }
    return None


def _check_stress_spike(signals: list, symbol: str) -> Optional[dict]:
    """Alert if stress score jumped > 0.15 in one bar."""
    curr_score = signals[-1].get("ensemble_score", 0)
    prev_score = signals[-2].get("ensemble_score", 0)

    if curr_score is not None and prev_score is not None:
        jump = curr_score - prev_score
        if jump > 0.15:
            return {
                "type": "stress_spike",
                "severity": "warning",
                "message": f"Stress spike detected for {symbol}: ensemble score jumped "
                           f"{prev_score:.3f} -> {curr_score:.3f} (+{jump:.3f}). Monitor closely.",
            }
    return None


def _check_breakout(signals: list, symbol: str) -> Optional[dict]:
    """Alert if a breakout was detected in the latest signal."""
    signal = signals[-1]
    features = {}
    if signal.get("features_json"):
        try:
            features = json.loads(signal["features_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    bo = features.get("breakout", {})
    if bo.get("breakout_detected"):
        direction = bo.get("direction", "Unknown")
        level = bo.get("broken_level", 0)
        confidence = bo.get("confidence", 0)

        bq = features.get("breakout_quality", {})
        quality = bq.get("quality_signal", "")

        return {
            "type": "breakout_detected",
            "severity": "info" if "WATCH" in quality or "AVOID" in quality else "warning",
            "message": f"{direction} breakout detected for {symbol} at {level:.2f} "
                       f"(confidence: {confidence * 100:.0f}%, quality: {quality}).",
        }
    return None


def _check_coupling_divergence(signals: list, symbol: str) -> Optional[dict]:
    """Alert if coupling dropped sharply over recent bars."""
    if len(signals) < 5:
        return None

    recent_coupling = [s.get("coupling_score", 0) for s in signals[-5:]]
    if all(c is not None for c in recent_coupling) and recent_coupling[0] is not None:
        drop = recent_coupling[0] - recent_coupling[-1]
        if drop > 0.2:
            return {
                "type": "coupling_divergence",
                "severity": "critical" if drop > 0.35 else "warning",
                "message": f"Coupling divergence detected for {symbol}: dropped {drop:.3f} over last 5 bars. "
                           f"{'Critical decoupling!' if drop > 0.35 else 'Market sectors diverging.'}",
            }
    return None


def _regime_rank(regime: str) -> int:
    return {"GREEN": 0, "AMBER": 1, "RED": 2}.get(regime, 1)
