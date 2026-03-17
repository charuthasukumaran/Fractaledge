"""
Sector Rotation Map — computes relative strength of NIFTY sectors.
"""
import logging
import time
import yfinance as yf

logger = logging.getLogger(__name__)

NIFTY_SECTORS = {
    "^CNXIT": "IT",
    "^CNXFIN": "Finance",
    "^CNXPHARMA": "Pharma",
    "^CNXAUTO": "Auto",
    "^CNXMETAL": "Metal",
    "^CNXENERGY": "Energy",
    "^CNXREALTY": "Realty",
    "^CNXFMCG": "FMCG",
    "^CNXINFRA": "Infra",
    "^CNXPSUBANK": "PSU Bank",
}

_sector_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 300  # 5 minutes


def compute_sector_rotation(benchmark: str = "^NSEI", period: str = "1mo") -> dict:
    """
    Compute relative strength of each sector vs the benchmark.
    Results cached for 5 minutes.
    """
    now = time.time()
    if _sector_cache["data"] and (now - _sector_cache["timestamp"]) < CACHE_TTL:
        return _sector_cache["data"]

    try:
        bench_ticker = yf.Ticker(benchmark)
        bench_hist = bench_ticker.history(period=period)
        if bench_hist.empty or len(bench_hist) < 2:
            return {"sectors": [], "benchmark_return": 0, "error": "No benchmark data"}

        bench_return = ((bench_hist["Close"].iloc[-1] / bench_hist["Close"].iloc[0]) - 1) * 100
    except Exception as e:
        logger.error(f"Sector benchmark error: {e}")
        return {"sectors": [], "benchmark_return": 0, "error": str(e)}

    sectors = []
    for symbol, name in NIFTY_SECTORS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)
            if hist.empty or len(hist) < 2:
                continue

            sector_return = ((hist["Close"].iloc[-1] / hist["Close"].iloc[0]) - 1) * 100
            relative_strength = sector_return - bench_return

            if relative_strength > 2:
                trend = "outperforming"
            elif relative_strength < -2:
                trend = "underperforming"
            else:
                trend = "inline"

            sectors.append({
                "name": name,
                "symbol": symbol,
                "return_pct": round(sector_return, 2),
                "relative_strength": round(relative_strength, 2),
                "trend": trend,
                "current_price": round(float(hist["Close"].iloc[-1]), 2),
            })
        except Exception as e:
            logger.warning(f"Sector {name} ({symbol}) error: {e}")
            continue

    sectors.sort(key=lambda x: x["relative_strength"], reverse=True)

    result = {
        "sectors": sectors,
        "benchmark_return": round(bench_return, 2),
        "benchmark": benchmark,
        "period": period,
    }

    _sector_cache["data"] = result
    _sector_cache["timestamp"] = now

    return result
