"""
Market Data Client — uses Yahoo Finance (no API key needed).
Supports any stock symbol available on Yahoo Finance.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import yfinance as yf

from config import config

logger = logging.getLogger(__name__)

# ── Curated stock lists for the dropdown ──────────────────────────

NIFTY_50_STOCKS = {
    "^NSEI": "NIFTY 50",
    "^NSEBANK": "BANK NIFTY",
    "RELIANCE.NS": "Reliance Industries",
    "TCS.NS": "TCS",
    "HDFCBANK.NS": "HDFC Bank",
    "INFY.NS": "Infosys",
    "ICICIBANK.NS": "ICICI Bank",
    "HINDUNILVR.NS": "Hindustan Unilever",
    "ITC.NS": "ITC",
    "SBIN.NS": "SBI",
    "BHARTIARTL.NS": "Bharti Airtel",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",
    "LT.NS": "Larsen & Toubro",
    "AXISBANK.NS": "Axis Bank",
    "ASIANPAINT.NS": "Asian Paints",
    "MARUTI.NS": "Maruti Suzuki",
    "TITAN.NS": "Titan Company",
    "SUNPHARMA.NS": "Sun Pharma",
    "BAJFINANCE.NS": "Bajaj Finance",
    "WIPRO.NS": "Wipro",
    "ULTRACEMCO.NS": "UltraTech Cement",
    "HCLTECH.NS": "HCL Technologies",
    "ONGC.NS": "ONGC",
    "NTPC.NS": "NTPC",
    "POWERGRID.NS": "Power Grid Corp",
    "TATAMOTORS.NS": "Tata Motors",
    "TATASTEEL.NS": "Tata Steel",
    "M&M.NS": "Mahindra & Mahindra",
    "ADANIENT.NS": "Adani Enterprises",
    "ADANIPORTS.NS": "Adani Ports",
    "BAJAJFINSV.NS": "Bajaj Finserv",
    "NESTLEIND.NS": "Nestle India",
    "JSWSTEEL.NS": "JSW Steel",
    "TECHM.NS": "Tech Mahindra",
    "DRREDDY.NS": "Dr Reddy's Labs",
    "INDUSINDBK.NS": "IndusInd Bank",
    "CIPLA.NS": "Cipla",
    "COALINDIA.NS": "Coal India",
    "BPCL.NS": "BPCL",
    "GRASIM.NS": "Grasim Industries",
    "DIVISLAB.NS": "Divi's Laboratories",
    "EICHERMOT.NS": "Eicher Motors",
    "APOLLOHOSP.NS": "Apollo Hospitals",
    "HEROMOTOCO.NS": "Hero MotoCorp",
    "TATACONSUM.NS": "Tata Consumer",
    "SBILIFE.NS": "SBI Life Insurance",
    "BRITANNIA.NS": "Britannia",
    "HINDALCO.NS": "Hindalco",
    "BAJAJ-AUTO.NS": "Bajaj Auto",
    "LTIM.NS": "LTIMindtree",
}

GLOBAL_STOCKS = {
    "^GSPC": "S&P 500",
    "^DJI": "Dow Jones",
    "^IXIC": "NASDAQ Composite",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet (Google)",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase",
    "V": "Visa",
    "WMT": "Walmart",
}

COMMODITIES = {
    "GC=F": "Gold",
    "SI=F": "Silver",
    "CL=F": "Crude Oil (WTI)",
    "BZ=F": "Brent Crude Oil",
    "NG=F": "Natural Gas",
    "HG=F": "Copper",
    "PL=F": "Platinum",
    "PA=F": "Palladium",
    "ZC=F": "Corn",
    "ZW=F": "Wheat",
    "ZS=F": "Soybeans",
    "CT=F": "Cotton",
    "KC=F": "Coffee",
    "SB=F": "Sugar",
    "CC=F": "Cocoa",
}

CRYPTO = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "BNB-USD": "Binance Coin",
    "SOL-USD": "Solana",
    "XRP-USD": "XRP (Ripple)",
    "ADA-USD": "Cardano",
    "DOGE-USD": "Dogecoin",
    "DOT-USD": "Polkadot",
    "AVAX-USD": "Avalanche",
    "MATIC-USD": "Polygon",
    "LINK-USD": "Chainlink",
    "UNI-USD": "Uniswap",
    "SHIB-USD": "Shiba Inu",
    "LTC-USD": "Litecoin",
    "ATOM-USD": "Cosmos",
}

ALL_STOCKS = {**NIFTY_50_STOCKS, **GLOBAL_STOCKS, **COMMODITIES, **CRYPTO}


def get_stock_list():
    """Return categorized stock lists for the UI dropdown."""
    return {
        "nifty50": [{"symbol": k, "name": v} for k, v in NIFTY_50_STOCKS.items()],
        "global": [{"symbol": k, "name": v} for k, v in GLOBAL_STOCKS.items()],
        "commodities": [{"symbol": k, "name": v} for k, v in COMMODITIES.items()],
        "crypto": [{"symbol": k, "name": v} for k, v in CRYPTO.items()],
    }


def search_stocks(query: str) -> list[dict]:
    """Search stocks by symbol or name."""
    query_up = query.upper().strip()
    results = []
    for sym, name in ALL_STOCKS.items():
        if query_up in sym.upper() or query_up in name.upper():
            results.append({"symbol": sym, "name": name})
    return results[:20]


class MarketDataClient:
    """Yahoo Finance data client — supports any symbol."""

    def __init__(self, symbol: str = "^NSEI"):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        self.name = ALL_STOCKS.get(symbol, symbol)
        logger.info(f"MarketDataClient initialized for {symbol} ({self.name})")

    def get_candles_history(self, days: int = None, interval: str = "5m") -> list[dict]:
        """Fetch historical candles for the configured symbol."""
        days = min(days or config.backfill_days, 59)
        logger.info(f"Fetching {days} days of {interval} candles for {self.symbol}")

        try:
            df = self.ticker.history(period=f"{days}d", interval=interval)

            if df.empty and interval != "1d":
                logger.warning(f"No {interval} data for {self.symbol}, trying daily...")
                df = self.ticker.history(period=f"{min(days*5, 365)}d", interval="1d")
            if df.empty:
                logger.warning(f"No data returned for {self.symbol}")
                return []

            candles = []
            for ts, row in df.iterrows():
                candles.append({
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]),
                    "source": "yahoo",
                })

            logger.info(f"Fetched {len(candles)} candles for {self.symbol}")
            return candles

        except Exception as e:
            logger.error(f"Yahoo Finance error for {self.symbol}: {e}")
            return []

    def get_latest_candles(self, lookback_hours: int = 4, interval: str = "5m") -> list[dict]:
        """Fetch the most recent candles."""
        try:
            df = self.ticker.history(period="5d", interval=interval)
            if df.empty:
                df = self.ticker.history(period="5d", interval="1d")
            if df.empty:
                return []

            candles = []
            for ts, row in df.iterrows():
                candles.append({
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]),
                    "source": "yahoo",
                })
            return candles

        except Exception as e:
            logger.error(f"Yahoo Finance latest fetch error for {self.symbol}: {e}")
            return []

    def get_secondary_candles(self, symbol: str = None, days: int = None, interval: str = None) -> list[dict]:
        """
        Fetch candles for a secondary instrument (for MFDCCA coupling).
        Uses NIFTY as the pairing index for NSE stocks, S&P500 for US stocks.
        """
        days = min(days or config.backfill_days, 59)

        if self.symbol.endswith(".NS") or self.symbol.startswith("^NSE"):
            pairing_symbols = [
                ("^NSEI", "5m"), ("^NSEI", "1d"),
                ("^NSEBANK", "5m"), ("^NSEBANK", "1d"),
                ("^BSESN", "5m"), ("^BSESN", "1d"),
            ]
        elif self.symbol.endswith("-USD"):
            # Crypto — pair with Bitcoin as the market leader
            pairing_symbols = [
                ("BTC-USD", "5m"), ("BTC-USD", "1d"),
                ("ETH-USD", "5m"), ("ETH-USD", "1d"),
            ]
        elif self.symbol.endswith("=F"):
            # Commodities — pair with Gold as the safe-haven benchmark
            pairing_symbols = [
                ("GC=F", "5m"), ("GC=F", "1d"),
                ("CL=F", "5m"), ("CL=F", "1d"),
            ]
        else:
            pairing_symbols = [
                ("^GSPC", "5m"), ("^GSPC", "1d"),
                ("^DJI", "5m"), ("^DJI", "1d"),
            ]

        if symbol and interval:
            pairing_symbols = [(symbol, interval)] + pairing_symbols

        for sym, intv in pairing_symbols:
            if sym == self.symbol:
                continue
            try:
                ticker = yf.Ticker(sym)
                period = f"{days}d" if intv in ("5m", "15m") else f"{min(days * 5, 365)}d"
                logger.info(f"MFDCCA: trying {sym} @ {intv}...")

                df = ticker.history(period=period, interval=intv)
                if df.empty:
                    continue

                candles = []
                for ts, row in df.iterrows():
                    candles.append({
                        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
                        "volume": int(row["Volume"]),
                        "source": "yahoo",
                    })

                if len(candles) >= config.mfdfa.min_bars:
                    logger.info(f"MFDCCA: got {len(candles)} candles from {sym} @ {intv}")
                    return candles

            except Exception as e:
                logger.warning(f"MFDCCA: {sym} @ {intv} failed - {e}")
                continue

        logger.error("MFDCCA: ALL secondary symbol attempts failed")
        return []

    def get_stock_info(self) -> dict:
        """Get basic stock info for display."""
        try:
            info = self.ticker.info
            return {
                "symbol": self.symbol,
                "name": info.get("shortName", info.get("longName", self.name)),
                "currency": info.get("currency", "INR"),
                "exchange": info.get("exchange", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "price": info.get("regularMarketPrice", info.get("previousClose")),
                "change": info.get("regularMarketChange"),
                "change_pct": info.get("regularMarketChangePercent"),
                "day_high": info.get("dayHigh"),
                "day_low": info.get("dayLow"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            }
        except Exception as e:
            logger.error(f"Failed to get info for {self.symbol}: {e}")
            return {"symbol": self.symbol, "name": self.name}
