"""
Angel One SmartAPI Client — Real-time market data via SmartAPI.
===============================================================
Provides the same interface as MarketDataClient (data_client.py)
so it can be used as a drop-in replacement for Yahoo Finance.

Features:
  - REST API for historical candles (OHLCV)
  - WebSocket streaming for live tick data (LTP, quotes)
  - TOTP-based authentication
  - Symbol token mapping (Yahoo symbol → Angel One token)
  - Rate limiting and retry logic
"""
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

import pyotp

from config import config

logger = logging.getLogger(__name__)

# ── Symbol Token Mapping ─────────────────────────────────────────
# Maps Yahoo Finance symbols to Angel One instrument tokens.
# Token values are from the Angel One OpenAPI Scrip Master.
# Format: "yahoo_symbol": {"token": "...", "exchange": "NSE/BSE", "tradingsymbol": "..."}

ANGEL_TOKEN_MAP = {
    # ── Indices ──
    "^NSEI":        {"token": "99926000", "exchange": "NSE", "tradingsymbol": "NIFTY",       "name": "NIFTY 50"},
    "^NSEBANK":     {"token": "99926009", "exchange": "NSE", "tradingsymbol": "BANKNIFTY",   "name": "BANK NIFTY"},
    # ── NIFTY 50 Stocks ──
    "RELIANCE.NS":  {"token": "2885",  "exchange": "NSE", "tradingsymbol": "RELIANCE-EQ",  "name": "Reliance Industries"},
    "TCS.NS":       {"token": "11536", "exchange": "NSE", "tradingsymbol": "TCS-EQ",       "name": "TCS"},
    "HDFCBANK.NS":  {"token": "1333",  "exchange": "NSE", "tradingsymbol": "HDFCBANK-EQ",  "name": "HDFC Bank"},
    "INFY.NS":      {"token": "1594",  "exchange": "NSE", "tradingsymbol": "INFY-EQ",      "name": "Infosys"},
    "ICICIBANK.NS": {"token": "4963",  "exchange": "NSE", "tradingsymbol": "ICICIBANK-EQ", "name": "ICICI Bank"},
    "HINDUNILVR.NS":{"token": "1394",  "exchange": "NSE", "tradingsymbol": "HINDUNILVR-EQ","name": "Hindustan Unilever"},
    "ITC.NS":       {"token": "1660",  "exchange": "NSE", "tradingsymbol": "ITC-EQ",       "name": "ITC"},
    "SBIN.NS":      {"token": "3045",  "exchange": "NSE", "tradingsymbol": "SBIN-EQ",      "name": "SBI"},
    "BHARTIARTL.NS":{"token": "10604", "exchange": "NSE", "tradingsymbol": "BHARTIARTL-EQ","name": "Bharti Airtel"},
    "KOTAKBANK.NS": {"token": "1922",  "exchange": "NSE", "tradingsymbol": "KOTAKBANK-EQ", "name": "Kotak Mahindra Bank"},
    "LT.NS":        {"token": "11483", "exchange": "NSE", "tradingsymbol": "LT-EQ",        "name": "Larsen & Toubro"},
    "AXISBANK.NS":  {"token": "5900",  "exchange": "NSE", "tradingsymbol": "AXISBANK-EQ",  "name": "Axis Bank"},
    "ASIANPAINT.NS":{"token": "236",   "exchange": "NSE", "tradingsymbol": "ASIANPAINT-EQ","name": "Asian Paints"},
    "MARUTI.NS":    {"token": "10999", "exchange": "NSE", "tradingsymbol": "MARUTI-EQ",    "name": "Maruti Suzuki"},
    "TITAN.NS":     {"token": "3506",  "exchange": "NSE", "tradingsymbol": "TITAN-EQ",     "name": "Titan Company"},
    "SUNPHARMA.NS": {"token": "3351",  "exchange": "NSE", "tradingsymbol": "SUNPHARMA-EQ", "name": "Sun Pharma"},
    "BAJFINANCE.NS":{"token": "317",   "exchange": "NSE", "tradingsymbol": "BAJFINANCE-EQ","name": "Bajaj Finance"},
    "WIPRO.NS":     {"token": "3787",  "exchange": "NSE", "tradingsymbol": "WIPRO-EQ",     "name": "Wipro"},
    "ULTRACEMCO.NS":{"token": "11532", "exchange": "NSE", "tradingsymbol": "ULTRACEMCO-EQ","name": "UltraTech Cement"},
    "HCLTECH.NS":   {"token": "7229",  "exchange": "NSE", "tradingsymbol": "HCLTECH-EQ",  "name": "HCL Technologies"},
    "ONGC.NS":      {"token": "2475",  "exchange": "NSE", "tradingsymbol": "ONGC-EQ",     "name": "ONGC"},
    "NTPC.NS":      {"token": "11630", "exchange": "NSE", "tradingsymbol": "NTPC-EQ",     "name": "NTPC"},
    "POWERGRID.NS": {"token": "14977", "exchange": "NSE", "tradingsymbol": "POWERGRID-EQ","name": "Power Grid Corp"},
    "TATAMOTORS.NS":{"token": "3456",  "exchange": "NSE", "tradingsymbol": "TATAMOTORS-EQ","name": "Tata Motors"},
    "TATASTEEL.NS": {"token": "3499",  "exchange": "NSE", "tradingsymbol": "TATASTEEL-EQ","name": "Tata Steel"},
    "M&M.NS":       {"token": "2031",  "exchange": "NSE", "tradingsymbol": "M&M-EQ",      "name": "Mahindra & Mahindra"},
    "ADANIENT.NS":  {"token": "25",    "exchange": "NSE", "tradingsymbol": "ADANIENT-EQ", "name": "Adani Enterprises"},
    "ADANIPORTS.NS":{"token": "15083", "exchange": "NSE", "tradingsymbol": "ADANIPORTS-EQ","name": "Adani Ports"},
    "BAJAJFINSV.NS":{"token": "16675", "exchange": "NSE", "tradingsymbol": "BAJAJFINSV-EQ","name": "Bajaj Finserv"},
    "NESTLEIND.NS": {"token": "17963", "exchange": "NSE", "tradingsymbol": "NESTLEIND-EQ","name": "Nestle India"},
    "JSWSTEEL.NS":  {"token": "11723", "exchange": "NSE", "tradingsymbol": "JSWSTEEL-EQ", "name": "JSW Steel"},
    "TECHM.NS":     {"token": "13538", "exchange": "NSE", "tradingsymbol": "TECHM-EQ",    "name": "Tech Mahindra"},
    "DRREDDY.NS":   {"token": "881",   "exchange": "NSE", "tradingsymbol": "DRREDDY-EQ",  "name": "Dr Reddy's Labs"},
    "INDUSINDBK.NS":{"token": "5258",  "exchange": "NSE", "tradingsymbol": "INDUSINDBK-EQ","name": "IndusInd Bank"},
    "CIPLA.NS":     {"token": "694",   "exchange": "NSE", "tradingsymbol": "CIPLA-EQ",    "name": "Cipla"},
    "COALINDIA.NS": {"token": "20374", "exchange": "NSE", "tradingsymbol": "COALINDIA-EQ","name": "Coal India"},
    "BPCL.NS":      {"token": "526",   "exchange": "NSE", "tradingsymbol": "BPCL-EQ",     "name": "BPCL"},
    "GRASIM.NS":    {"token": "1232",  "exchange": "NSE", "tradingsymbol": "GRASIM-EQ",   "name": "Grasim Industries"},
    "DIVISLAB.NS":  {"token": "10940", "exchange": "NSE", "tradingsymbol": "DIVISLAB-EQ", "name": "Divi's Laboratories"},
    "EICHERMOT.NS": {"token": "910",   "exchange": "NSE", "tradingsymbol": "EICHERMOT-EQ","name": "Eicher Motors"},
    "APOLLOHOSP.NS":{"token": "157",   "exchange": "NSE", "tradingsymbol": "APOLLOHOSP-EQ","name": "Apollo Hospitals"},
    "HEROMOTOCO.NS":{"token": "1348",  "exchange": "NSE", "tradingsymbol": "HEROMOTOCO-EQ","name": "Hero MotoCorp"},
    "TATACONSUM.NS":{"token": "3432",  "exchange": "NSE", "tradingsymbol": "TATACONSUM-EQ","name": "Tata Consumer"},
    "SBILIFE.NS":   {"token": "21808", "exchange": "NSE", "tradingsymbol": "SBILIFE-EQ",  "name": "SBI Life Insurance"},
    "BRITANNIA.NS": {"token": "547",   "exchange": "NSE", "tradingsymbol": "BRITANNIA-EQ","name": "Britannia"},
    "HINDALCO.NS":  {"token": "1363",  "exchange": "NSE", "tradingsymbol": "HINDALCO-EQ", "name": "Hindalco"},
    "BAJAJ-AUTO.NS":{"token": "16669", "exchange": "NSE", "tradingsymbol": "BAJAJ-AUTO-EQ","name": "Bajaj Auto"},
    "LTIM.NS":      {"token": "17818", "exchange": "NSE", "tradingsymbol": "LTIM-EQ",     "name": "LTIMindtree"},
}

# Interval mapping: our standard → SmartAPI interval names
INTERVAL_MAP = {
    "5m":  "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h":  "ONE_HOUR",
    "1d":  "ONE_DAY",
}


def get_angel_token(yahoo_symbol: str) -> Optional[dict]:
    """Lookup Angel One token info for a Yahoo Finance symbol."""
    return ANGEL_TOKEN_MAP.get(yahoo_symbol)


def is_angel_symbol(yahoo_symbol: str) -> bool:
    """Check if we have an Angel One token mapping for this symbol."""
    return yahoo_symbol in ANGEL_TOKEN_MAP


# ═══════════════════════════════════════════════════════════════════
# SmartAPI REST Client
# ═══════════════════════════════════════════════════════════════════

class SmartAPIClient:
    """Low-level authenticated SmartAPI client with retry logic."""

    def __init__(self):
        from SmartApi import SmartConnect
        self.api = SmartConnect(api_key=config.smartapi.api_key)
        self.auth_token = None
        self.feed_token = None
        self.client_id = config.smartapi.client_id
        self._last_request_time = 0
        self._logged_in = False

    def login(self):
        """Authenticate with SmartAPI using TOTP."""
        totp = pyotp.TOTP(config.smartapi.totp_secret).now()
        data = self.api.generateSession(
            clientCode=config.smartapi.client_id,
            password=config.smartapi.password,
            totp=totp,
        )
        if data and data.get("status"):
            self.auth_token = data["data"]["jwtToken"]
            self.feed_token = self.api.getfeedToken()
            self._logged_in = True
            logger.info("SmartAPI login successful")
        else:
            self._logged_in = False
            msg = data.get("message", "Unknown error") if data else "No response"
            raise ConnectionError(f"SmartAPI login failed: {msg}")

    @property
    def is_logged_in(self):
        return self._logged_in

    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self._last_request_time
        min_interval = 1.0 / config.smartapi.requests_per_second
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def _retry(self, func, *args, **kwargs):
        """Retry wrapper with exponential backoff."""
        for attempt in range(config.smartapi.max_retries):
            try:
                self._rate_limit()
                result = func(*args, **kwargs)
                if isinstance(result, dict) and result.get("errorcode") == "AB1004":
                    raise Exception("Rate limited (AB1004)")
                return result
            except Exception as e:
                wait = config.smartapi.retry_backoff_base ** attempt
                logger.warning(f"Attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
                if attempt == config.smartapi.max_retries - 1:
                    raise

    def get_candle_data(self, from_date: str, to_date: str,
                        interval: str = "FIVE_MINUTE",
                        exchange: str = "NSE",
                        token: str = "99926000") -> list:
        """Fetch historical candle data. Returns list of candle dicts."""
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
        }

        result = self._retry(self.api.getCandleData, params)

        if not result or not result.get("data"):
            logger.warning(f"No candle data for {token} ({from_date} → {to_date})")
            return []

        candles = []
        for row in result["data"]:
            # SmartAPI returns: [timestamp, open, high, low, close, volume]
            candles.append({
                "timestamp": row[0].replace("+05:30", "").replace("T", "T") if isinstance(row[0], str) else str(row[0]),
                "open": round(float(row[1]), 2),
                "high": round(float(row[2]), 2),
                "low": round(float(row[3]), 2),
                "close": round(float(row[4]), 2),
                "volume": int(row[5]) if len(row) > 5 else 0,
                "source": "angelone",
            })
        return candles

    def get_candles_chunked(self, start_date: datetime, end_date: datetime,
                             interval: str = "FIVE_MINUTE",
                             exchange: str = "NSE",
                             token: str = "99926000",
                             chunk_days: int = 5) -> list:
        """Fetch candles in chunks to handle the ~500 record limit."""
        all_candles = []
        current = start_date

        while current < end_date:
            chunk_end = min(current + timedelta(days=chunk_days), end_date)
            from_str = current.strftime("%Y-%m-%d %H:%M")
            to_str = chunk_end.strftime("%Y-%m-%d %H:%M")

            logger.info(f"  Fetching candles: {from_str} → {to_str}")
            candles = self.get_candle_data(from_str, to_str, interval, exchange, token)
            all_candles.extend(candles)

            current = chunk_end + timedelta(minutes=1)

        return all_candles

    def get_ltp(self, exchange: str, token: str) -> Optional[dict]:
        """Get Last Traded Price for a symbol."""
        try:
            self._rate_limit()
            data = self.api.ltpData(exchange, token, token)
            if data and data.get("data"):
                return data["data"]
        except Exception as e:
            logger.error(f"LTP fetch error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# High-Level Data Client (same interface as MarketDataClient)
# ═══════════════════════════════════════════════════════════════════

class AngelOneDataClient:
    """
    Angel One data client — same interface as MarketDataClient.
    Drop-in replacement for Yahoo Finance data fetching.
    """

    def __init__(self, symbol: str = "^NSEI", api_client: SmartAPIClient = None):
        self.symbol = symbol
        self.api_client = api_client

        # Resolve Angel One token info
        self.token_info = get_angel_token(symbol)
        if not self.token_info:
            raise ValueError(
                f"Symbol '{symbol}' not found in Angel One token map. "
                f"Only NSE stocks and NIFTY indices are supported."
            )

        self.token = self.token_info["token"]
        self.exchange = self.token_info["exchange"]
        self.name = self.token_info.get("name", symbol)

        from data_client import ALL_STOCKS
        self._all_stocks = ALL_STOCKS

    def get_candles_history(self, days: int = None, interval: str = "5m") -> list:
        """Fetch historical candles — mirrors MarketDataClient.get_candles_history()."""
        days = days or config.backfill_days

        # Map interval
        smart_interval = INTERVAL_MAP.get(interval, "FIVE_MINUTE")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        logger.info(f"Angel One: fetching {days}d of {interval} candles for {self.symbol}")

        try:
            candles = self.api_client.get_candles_chunked(
                start_date, end_date,
                interval=smart_interval,
                exchange=self.exchange,
                token=self.token,
                chunk_days=5,
            )
            logger.info(f"Angel One: got {len(candles)} candles for {self.symbol}")

            # Normalize timestamps to ISO format
            for c in candles:
                ts = c["timestamp"]
                # Remove timezone info for consistency with Yahoo client
                if "+05:30" in ts:
                    ts = ts.replace("+05:30", "")
                if "T" not in ts:
                    ts = ts.replace(" ", "T")
                c["timestamp"] = ts

            return candles

        except Exception as e:
            logger.error(f"Angel One candle fetch error for {self.symbol}: {e}")
            return []

    def get_latest_candles(self, lookback_hours: int = 4, interval: str = "5m") -> list:
        """Fetch recent candles — mirrors MarketDataClient.get_latest_candles()."""
        smart_interval = INTERVAL_MAP.get(interval, "FIVE_MINUTE")

        # Fetch last 5 days (to ensure we have data even over weekends)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5)

        try:
            candles = self.api_client.get_candles_chunked(
                start_date, end_date,
                interval=smart_interval,
                exchange=self.exchange,
                token=self.token,
                chunk_days=5,
            )
            return candles
        except Exception as e:
            logger.error(f"Angel One latest candles error: {e}")
            return []

    def get_secondary_candles(self, symbol: str = None, days: int = None,
                               interval: str = None) -> list:
        """Fetch secondary index candles for MFDCCA — mirrors MarketDataClient."""
        days = days or config.backfill_days
        interval = interval or "5m"

        # Determine pairing index (same logic as Yahoo client)
        if symbol:
            sec_symbol = symbol
        elif self.symbol.endswith(".NS") or self.symbol.startswith("^NSE"):
            sec_symbol = "^NSEI" if self.symbol != "^NSEI" else "^NSEBANK"
        else:
            sec_symbol = "^NSEI"  # Default to NIFTY

        sec_token_info = get_angel_token(sec_symbol)
        if not sec_token_info:
            logger.warning(f"No Angel One token for secondary symbol: {sec_symbol}")
            return []

        smart_interval = INTERVAL_MAP.get(interval, "FIVE_MINUTE")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        try:
            candles = self.api_client.get_candles_chunked(
                start_date, end_date,
                interval=smart_interval,
                exchange=sec_token_info["exchange"],
                token=sec_token_info["token"],
                chunk_days=5,
            )
            logger.info(f"Angel One: got {len(candles)} secondary candles ({sec_symbol})")
            return candles
        except Exception as e:
            logger.error(f"Angel One secondary candles error: {e}")
            return []

    def get_stock_info(self) -> dict:
        """Get stock info — mirrors MarketDataClient.get_stock_info()."""
        info = {
            "name": self.name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "currency": "INR",
            "source": "angelone",
        }

        # Try to get live price
        try:
            ltp_data = self.api_client.get_ltp(self.exchange, self.token)
            if ltp_data:
                info["price"] = float(ltp_data.get("ltp", 0))
                info["open"] = float(ltp_data.get("open", 0))
                info["high"] = float(ltp_data.get("high", 0))
                info["low"] = float(ltp_data.get("low", 0))
                info["close"] = float(ltp_data.get("close", 0))
        except Exception as e:
            logger.error(f"Angel One stock info error: {e}")

        return info


# ═══════════════════════════════════════════════════════════════════
# WebSocket Manager for Live Price Streaming
# ═══════════════════════════════════════════════════════════════════

class WebSocketManager:
    """
    Manages WebSocket connection for live tick data.
    Thread-safe storage of latest prices for each subscribed symbol.
    """

    # Exchange type codes for SmartWebSocketV2
    EXCHANGE_NSE_CM = 1   # NSE Cash Market
    EXCHANGE_NSE_FO = 2   # NSE F&O
    EXCHANGE_BSE_CM = 3   # BSE Cash Market

    def __init__(self, api_client: SmartAPIClient):
        self.api_client = api_client
        self.ws = None
        self._latest_ticks = {}  # {yahoo_symbol: {ltp, timestamp, volume, ...}}
        self._lock = threading.Lock()
        self._subscribed_tokens = []  # List of token dicts
        self._running = False
        self._thread = None
        self._callbacks = []  # External tick callbacks

    def on_tick_callback(self, callback: Callable):
        """Register an external callback for tick data."""
        self._callbacks.append(callback)

    def _get_exchange_type(self, exchange: str) -> int:
        """Map exchange name to SmartWebSocket exchange type code."""
        return {
            "NSE": self.EXCHANGE_NSE_CM,
            "BSE": self.EXCHANGE_BSE_CM,
            "NFO": self.EXCHANGE_NSE_FO,
        }.get(exchange, self.EXCHANGE_NSE_CM)

    def subscribe(self, yahoo_symbol: str):
        """Subscribe to live ticks for a symbol."""
        token_info = get_angel_token(yahoo_symbol)
        if not token_info:
            logger.warning(f"Cannot subscribe to {yahoo_symbol}: no Angel One token")
            return

        token = token_info["token"]
        exchange_type = self._get_exchange_type(token_info["exchange"])

        # Store mapping for tick processing
        with self._lock:
            self._latest_ticks[yahoo_symbol] = {
                "token": token,
                "exchange_type": exchange_type,
                "ltp": 0, "timestamp": None, "volume": 0,
                "bid": 0, "ask": 0, "change": 0,
            }

        # If WebSocket is connected, subscribe immediately
        if self.ws and self._running:
            try:
                token_list = [{"exchangeType": exchange_type, "tokens": [token]}]
                self.ws.subscribe("abc", 1, token_list)
                logger.info(f"WebSocket: subscribed to {yahoo_symbol} ({token})")
            except Exception as e:
                logger.error(f"WebSocket subscribe error: {e}")

    def unsubscribe(self, yahoo_symbol: str):
        """Unsubscribe from live ticks."""
        with self._lock:
            tick_data = self._latest_ticks.pop(yahoo_symbol, None)

        if tick_data and self.ws and self._running:
            try:
                token_list = [{"exchangeType": tick_data["exchange_type"],
                               "tokens": [tick_data["token"]]}]
                self.ws.unsubscribe("abc", 1, token_list)
            except Exception:
                pass

    def get_ltp(self, yahoo_symbol: str) -> Optional[dict]:
        """Get latest tick data for a symbol (thread-safe)."""
        with self._lock:
            tick = self._latest_ticks.get(yahoo_symbol)
            if tick:
                return dict(tick)  # Return a copy
        return None

    def get_all_ticks(self) -> dict:
        """Get all latest ticks (thread-safe)."""
        with self._lock:
            return {k: dict(v) for k, v in self._latest_ticks.items()}

    def start(self):
        """Start WebSocket connection in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()
        logger.info("WebSocket manager started")

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self.ws:
            try:
                self.ws.close_connection()
            except Exception:
                pass
        self.ws = None
        logger.info("WebSocket manager stopped")

    def _run_ws(self):
        """Internal: run WebSocket with auto-reconnect."""
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")

            if self._running:
                logger.info("WebSocket reconnecting in 5 seconds...")
                time.sleep(5)

    def _connect_and_listen(self):
        """Internal: establish WebSocket connection."""
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        self.ws = SmartWebSocketV2(
            self.api_client.auth_token,
            self.api_client.client_id,
            self.api_client.feed_token,
            config.smartapi.api_key,
        )

        # Build token→symbol reverse map for tick processing
        token_to_symbol = {}
        with self._lock:
            for sym, data in self._latest_ticks.items():
                token_to_symbol[data["token"]] = sym

        def on_data(wsapp, message):
            try:
                self._handle_tick(message, token_to_symbol)
            except Exception as e:
                logger.error(f"Tick handler error: {e}")

        def on_open(wsapp):
            logger.info("WebSocket connected — subscribing to symbols")
            # Subscribe to all tracked symbols
            subscribe_list = []
            with self._lock:
                for sym, data in self._latest_ticks.items():
                    subscribe_list.append({
                        "exchangeType": data["exchange_type"],
                        "tokens": [data["token"]],
                    })

            if subscribe_list:
                try:
                    self.ws.subscribe("abc", 1, subscribe_list)
                    logger.info(f"WebSocket: subscribed to {len(subscribe_list)} symbols")
                except Exception as e:
                    logger.error(f"WebSocket bulk subscribe error: {e}")

        def on_error(wsapp, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(wsapp):
            logger.warning("WebSocket closed")

        self.ws.on_data = on_data
        self.ws.on_open = on_open
        self.ws.on_error = on_error
        self.ws.on_close = on_close

        self.ws.connect()

    def _handle_tick(self, message, token_to_symbol):
        """Process incoming tick data."""
        if not isinstance(message, dict):
            return

        token = str(message.get("token", ""))
        yahoo_symbol = token_to_symbol.get(token)

        if not yahoo_symbol:
            return

        ltp = message.get("last_traded_price", 0)
        # SmartAPI sends prices in paise (×100), divide for stocks
        # But indices are sent as-is — check if token is an index
        if ltp > 100000 and token not in ("99926000", "99926009"):
            ltp = ltp / 100.0

        tick_data = {
            "symbol": yahoo_symbol,
            "ltp": ltp,
            "timestamp": datetime.now().isoformat(),
            "volume": message.get("volume_trade_for_the_day", 0),
            "open": message.get("open_price_of_the_day", 0),
            "high": message.get("high_price_of_the_day", 0),
            "low": message.get("low_price_of_the_day", 0),
            "close": message.get("closed_price", 0),
            "change": message.get("change", 0),
        }

        with self._lock:
            if yahoo_symbol in self._latest_ticks:
                self._latest_ticks[yahoo_symbol].update(tick_data)

        # Fire external callbacks
        for cb in self._callbacks:
            try:
                cb(tick_data)
            except Exception as e:
                logger.error(f"Tick callback error: {e}")


# ═══════════════════════════════════════════════════════════════════
# Module-Level Singleton Management
# ═══════════════════════════════════════════════════════════════════

_api_client: Optional[SmartAPIClient] = None
_ws_manager: Optional[WebSocketManager] = None


def get_api_client() -> SmartAPIClient:
    """Get or create the singleton SmartAPI client."""
    global _api_client
    if _api_client is None:
        _api_client = SmartAPIClient()
    return _api_client


def get_ws_manager() -> WebSocketManager:
    """Get or create the singleton WebSocket manager."""
    global _ws_manager
    if _ws_manager is None:
        client = get_api_client()
        _ws_manager = WebSocketManager(client)
    return _ws_manager


def login() -> dict:
    """Login to SmartAPI and return status."""
    client = get_api_client()
    try:
        client.login()
        return {"status": "ok", "message": "Login successful", "logged_in": True}
    except Exception as e:
        return {"status": "error", "message": str(e), "logged_in": False}


def is_logged_in() -> bool:
    """Check if currently logged in."""
    return _api_client is not None and _api_client.is_logged_in


def get_status() -> dict:
    """Get Angel One integration status."""
    return {
        "configured": config.smartapi.is_configured(),
        "logged_in": is_logged_in(),
        "has_api_key": bool(config.smartapi.api_key),
        "has_client_id": bool(config.smartapi.client_id),
        "has_pin": bool(config.smartapi.password),
        "has_totp": bool(config.smartapi.totp_secret),
        "ws_running": _ws_manager is not None and _ws_manager._running,
    }
