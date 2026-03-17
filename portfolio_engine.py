"""
Portfolio Stress Test Engine
------------------------------
Aggregates fractal analysis across portfolio holdings.
Includes position size calculator.
"""
import json
import logging
from database import get_portfolio, get_latest_signal, get_candles

logger = logging.getLogger(__name__)


def compute_portfolio_stress(portfolio_id: int) -> dict:
    """
    Stress test a portfolio by aggregating fractal data from all holdings.

    Returns portfolio-level stress metrics and scenario analysis.
    """
    portfolio = get_portfolio(portfolio_id)
    if not portfolio:
        return {"error": "Portfolio not found"}

    holdings = portfolio.get("holdings", [])
    if not holdings:
        return {"error": "Portfolio has no holdings", "portfolio_name": portfolio["name"]}

    results = []
    total_value = 0
    weighted_stress = 0
    regime_counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    highest_risk = {"symbol": None, "stress": 0}

    for h in holdings:
        symbol = h["symbol"]
        quantity = h.get("quantity", 0)
        avg_price = h.get("avg_price", 0)

        signal = get_latest_signal(symbol=symbol)
        candles = get_candles(limit=1, symbol=symbol)
        current_price = candles[-1]["close"] if candles else avg_price

        holding_value = quantity * current_price
        total_value += holding_value

        if signal:
            regime = signal.get("regime_label", "UNKNOWN")
            stress = signal.get("ensemble_score", 0)
            hurst = signal.get("hurst_exponent", 0.5)

            if regime in regime_counts:
                regime_counts[regime] += holding_value

            if stress > highest_risk["stress"]:
                highest_risk = {"symbol": symbol, "stress": stress}

            pnl_pct = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0

            results.append({
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": round(avg_price, 2),
                "current_price": round(current_price, 2),
                "value": round(holding_value, 2),
                "pnl_pct": round(pnl_pct, 2),
                "regime": regime,
                "stress_score": round(stress, 4),
                "hurst": round(hurst, 4),
                "analyzed": True,
            })
        else:
            results.append({
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": round(avg_price, 2),
                "current_price": round(current_price, 2),
                "value": round(holding_value, 2),
                "pnl_pct": 0,
                "regime": "NOT_ANALYZED",
                "stress_score": 0,
                "hurst": 0,
                "analyzed": False,
            })

    # Compute weighted stress
    for r in results:
        if r["analyzed"] and total_value > 0:
            weight = r["value"] / total_value
            weighted_stress += r["stress_score"] * weight
            r["weight_pct"] = round(weight * 100, 2)
        else:
            r["weight_pct"] = 0

    # Regime distribution as percentages
    regime_dist = {}
    for regime in ["GREEN", "AMBER", "RED"]:
        regime_dist[regime] = round(regime_counts[regime] / total_value * 100, 1) if total_value > 0 else 0

    # Stress scenarios
    # Historical rough estimates: RED regime sees ~2-5% drawdowns per day, GREEN ~0.5%
    red_pct = regime_dist.get("RED", 0) + regime_dist.get("AMBER", 0) * 0.5
    estimated_drawdown = round(red_pct * 0.03, 2)  # ~3% of at-risk portion
    at_risk_value = round(total_value * estimated_drawdown / 100, 2)

    # Portfolio health score (simplified)
    if weighted_stress < 0.35:
        portfolio_health = min(100, int(90 - weighted_stress * 100))
    elif weighted_stress < 0.65:
        portfolio_health = int(60 - (weighted_stress - 0.35) * 100)
    else:
        portfolio_health = max(0, int(30 - (weighted_stress - 0.65) * 100))

    return {
        "portfolio_name": portfolio["name"],
        "portfolio_id": portfolio_id,
        "total_value": round(total_value, 2),
        "holdings": results,
        "aggregate": {
            "weighted_stress": round(weighted_stress, 4),
            "regime_distribution": regime_dist,
            "highest_risk_holding": highest_risk["symbol"],
            "portfolio_health_score": portfolio_health,
            "holdings_count": len(results),
            "analyzed_count": sum(1 for r in results if r["analyzed"]),
        },
        "stress_scenarios": {
            "if_red": {
                "estimated_drawdown_pct": estimated_drawdown,
                "at_risk_value": at_risk_value,
                "description": "Estimated loss if entire market enters RED regime",
            },
            "if_green": {
                "estimated_upside_pct": round((1 - weighted_stress) * 2, 2),
                "description": "Potential upside if market stabilizes to GREEN",
            },
        },
    }


def compute_position_size(
    account_size: float, risk_pct: float, entry_price: float, stoploss: float
) -> dict:
    """
    Calculate optimal position size based on risk parameters.

    Returns:
        {quantity, risk_amount, max_loss, risk_per_share}
    """
    if entry_price <= 0 or stoploss <= 0 or account_size <= 0:
        return {"error": "Invalid input values", "quantity": 0}

    risk_amount = account_size * (risk_pct / 100)
    risk_per_share = abs(entry_price - stoploss)

    if risk_per_share <= 0:
        return {"error": "Entry and stoploss are the same", "quantity": 0}

    quantity = int(risk_amount / risk_per_share)
    max_loss = round(quantity * risk_per_share, 2)
    position_value = round(quantity * entry_price, 2)
    position_pct = round(position_value / account_size * 100, 2) if account_size > 0 else 0

    return {
        "quantity": quantity,
        "risk_amount": round(risk_amount, 2),
        "risk_per_share": round(risk_per_share, 2),
        "max_loss": max_loss,
        "position_value": position_value,
        "position_pct": position_pct,
        "entry_price": round(entry_price, 2),
        "stoploss": round(stoploss, 2),
        "direction": "LONG" if entry_price > stoploss else "SHORT",
    }
