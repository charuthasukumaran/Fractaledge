"""
Database layer — Supports both SQLite (local dev) and PostgreSQL (production).
Set DATABASE_URL env var for PostgreSQL; omit it to use local SQLite.
"""
import os
import json
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

# Detect database backend from environment
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3


# ── Schema ────────────────────────────────────────────────────────────

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS candles (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL DEFAULT '^NSEI',
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER DEFAULT 0,
    source TEXT DEFAULT 'historical',
    UNIQUE(symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_sym_ts ON candles(symbol, timestamp);

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL DEFAULT '^NSEI',
    timestamp TEXT NOT NULL,
    stress_score REAL,
    coupling_score REAL,
    ensemble_score REAL,
    regime_label TEXT,
    hurst_exponent REAL,
    spectral_width REAL,
    features_json TEXT,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_sym_ts ON signals(symbol, timestamp);

CREATE TABLE IF NOT EXISTS ticks (
    id SERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    ltp REAL,
    volume INTEGER,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(timestamp);

CREATE TABLE IF NOT EXISTS health (
    id SERIAL PRIMARY KEY,
    service TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolios (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    holdings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    condition_json TEXT,
    triggered_at TEXT NOT NULL,
    message TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_sym ON alerts(symbol, triggered_at);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts(read, triggered_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id SERIAL PRIMARY KEY,
    alert_id INTEGER,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL,
    params_json TEXT,
    result_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    computation_time REAL
);
CREATE INDEX IF NOT EXISTS idx_backtest_sym ON backtest_results(symbol, strategy);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL DEFAULT '^NSEI',
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER DEFAULT 0,
    source TEXT DEFAULT 'historical',
    UNIQUE(symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_sym_ts ON candles(symbol, timestamp);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL DEFAULT '^NSEI',
    timestamp TEXT NOT NULL,
    stress_score REAL,
    coupling_score REAL,
    ensemble_score REAL,
    regime_label TEXT,
    hurst_exponent REAL,
    spectral_width REAL,
    features_json TEXT,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_sym_ts ON signals(symbol, timestamp);

CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ltp REAL,
    volume INTEGER,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(timestamp);

CREATE TABLE IF NOT EXISTS health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    holdings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    condition_json TEXT,
    triggered_at TEXT NOT NULL,
    message TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_sym ON alerts(symbol, triggered_at);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts(read, triggered_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    error_message TEXT,
    FOREIGN KEY (alert_id) REFERENCES alerts(id)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL,
    params_json TEXT,
    result_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    computation_time REAL
);
CREATE INDEX IF NOT EXISTS idx_backtest_sym ON backtest_results(symbol, strategy);
"""


# ── Connection Helpers ────────────────────────────────────────────────

def _get_sqlite_path():
    from config import config
    return config.db_path


def init_db():
    """Initialize the database schema."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(_SCHEMA_POSTGRES)
        conn.commit()
        conn.close()
        logger.info("PostgreSQL database initialized")
    else:
        path = _get_sqlite_path()
        conn = sqlite3.connect(path)
        conn.executescript(_SCHEMA_SQLITE)
        conn.commit()
        conn.close()
        logger.info(f"SQLite database initialized at {path}")


@contextmanager
def get_db():
    """Context manager for database connections. Works with both SQLite and PostgreSQL."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        try:
            yield _PgConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        path = _get_sqlite_path()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield _SqliteConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class _PgConnection:
    """Wrapper around psycopg2 connection to provide a unified interface."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        """Execute a query, converting ? placeholders to %s for PostgreSQL."""
        query = query.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        return cur

    def executemany(self, query, params_list):
        """Execute many, converting ? to %s and :name to %(name)s."""
        query = query.replace("?", "%s")
        # Convert :name style params to %(name)s for psycopg2
        import re
        query = re.sub(r':(\w+)', r'%(\1)s', query)
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.executemany(query, params_list)
        return cur


class _SqliteConnection:
    """Thin wrapper around sqlite3 connection for unified interface."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        if params:
            return self._conn.execute(query, params)
        return self._conn.execute(query)

    def executemany(self, query, params_list):
        return self._conn.executemany(query, params_list)


# ── Candle Operations ──────────────────────────────────────────────

def upsert_candles(candles: list[dict], symbol: str = "^NSEI"):
    """Insert or ignore candles (dedup by symbol+timestamp)."""
    if not candles:
        return 0
    rows = [{"symbol": symbol, **c} for c in candles]
    with get_db() as conn:
        if USE_POSTGRES:
            cur = conn._conn.cursor()
            count = 0
            for r in rows:
                cur.execute(
                    """INSERT INTO candles (symbol, timestamp, open, high, low, close, volume, source)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, timestamp) DO NOTHING""",
                    (r["symbol"], r["timestamp"], r["open"], r["high"],
                     r["low"], r["close"], r["volume"], r.get("source", "historical")),
                )
                count += cur.rowcount
            return count
        else:
            cursor = conn.executemany(
                """INSERT OR IGNORE INTO candles (symbol, timestamp, open, high, low, close, volume, source)
                   VALUES (:symbol, :timestamp, :open, :high, :low, :close, :volume, :source)""",
                rows,
            )
            return cursor.rowcount


def get_candles(start: str = None, end: str = None, limit: int = 2000, symbol: str = "^NSEI") -> list[dict]:
    """Fetch candles for a symbol, optionally filtered by time range."""
    with get_db() as conn:
        query = "SELECT * FROM candles"
        params = []
        conditions = ["symbol = ?"]
        params.append(symbol)
        if start:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end:
            conditions.append("timestamp <= ?")
            params.append(end)
        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_latest_candle_timestamp(symbol: str = "^NSEI") -> Optional[str]:
    """Get the most recent candle timestamp for a symbol."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT timestamp FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        return row["timestamp"] if row else None


# ── Signal Operations ──────────────────────────────────────────────

def insert_signal(signal: dict, symbol: str = "^NSEI"):
    """Insert a computed signal for a symbol."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO signals
               (symbol, timestamp, stress_score, coupling_score, ensemble_score,
                regime_label, hurst_exponent, spectral_width, features_json, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, signal["timestamp"], signal["stress_score"], signal["coupling_score"],
             signal["ensemble_score"], signal["regime_label"], signal["hurst_exponent"],
             signal["spectral_width"], signal["features_json"], signal["computed_at"]),
        )


def get_signals(limit: int = 500, symbol: str = "^NSEI") -> list[dict]:
    """Fetch recent signals for a symbol."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_latest_signal(symbol: str = "^NSEI") -> Optional[dict]:
    """Get the most recent signal for a symbol."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        return dict(row) if row else None


def get_analyzed_symbols() -> list[str]:
    """Get list of symbols that have at least one signal."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM signals ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]


# ── Health Operations ──────────────────────────────────────────────

def log_health(service: str, status: str, message: str = ""):
    """Log a health check entry."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO health (service, status, message, checked_at) VALUES (?, ?, ?, ?)",
            (service, status, message, datetime.utcnow().isoformat()),
        )


# ── Portfolio Operations ──────────────────────────────────────────

def create_portfolio(name: str, holdings: list) -> int:
    """Create a portfolio. Returns id."""
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO portfolios (name, holdings_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, json.dumps(holdings), now, now),
        )
        if USE_POSTGRES:
            # psycopg2 cursor doesn't have lastrowid; fetch it
            cursor.execute("SELECT lastval()")
            return cursor.fetchone()[0] if not isinstance(cursor.fetchone(), dict) else cursor.fetchone()["lastval"]
        return cursor.lastrowid


def get_portfolio(portfolio_id: int) -> Optional[dict]:
    """Get a portfolio by id."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["holdings"] = json.loads(d.pop("holdings_json", "[]"))
        return d


def get_portfolios() -> list:
    """List all portfolios."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM portfolios ORDER BY updated_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["holdings"] = json.loads(d.pop("holdings_json", "[]"))
            result.append(d)
        return result


def update_portfolio(portfolio_id: int, name: str = None, holdings: list = None):
    """Update portfolio name and/or holdings."""
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        if name is not None:
            conn.execute("UPDATE portfolios SET name = ?, updated_at = ? WHERE id = ?", (name, now, portfolio_id))
        if holdings is not None:
            conn.execute("UPDATE portfolios SET holdings_json = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(holdings), now, portfolio_id))


def delete_portfolio(portfolio_id: int):
    """Delete a portfolio."""
    with get_db() as conn:
        conn.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))


# ── Alert Operations ──────────────────────────────────────────────

def insert_alert(symbol: str, alert_type: str, message: str, severity: str = "info", condition_json: str = None):
    """Insert a new alert."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO alerts (symbol, alert_type, condition_json, triggered_at, message, severity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, alert_type, condition_json, datetime.utcnow().isoformat(), message, severity),
        )


def get_alerts(symbol: str = None, unread_only: bool = False, limit: int = 50) -> list:
    """Fetch alerts, optionally filtered."""
    with get_db() as conn:
        conditions = []
        params = []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if unread_only:
            conditions.append("read = 0")
        query = "SELECT * FROM alerts"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY triggered_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def mark_alert_read(alert_id: int):
    """Mark an alert as read."""
    with get_db() as conn:
        conn.execute("UPDATE alerts SET read = 1 WHERE id = ?", (alert_id,))


def mark_all_alerts_read(symbol: str = None):
    """Mark all alerts as read, optionally filtered by symbol."""
    with get_db() as conn:
        if symbol:
            conn.execute("UPDATE alerts SET read = 1 WHERE symbol = ? AND read = 0", (symbol,))
        else:
            conn.execute("UPDATE alerts SET read = 1 WHERE read = 0")


def get_unread_alert_count() -> int:
    """Count unread alerts."""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM alerts WHERE read = 0").fetchone()
        return row["cnt"] if row else 0


# ── Notification Log Operations ──────────────────────────────────

def log_notification(alert_id: int, channel: str, status: str, error_message: str = None):
    """Log a notification send attempt."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO notification_log (alert_id, channel, status, sent_at, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (alert_id, channel, status, datetime.utcnow().isoformat(), error_message),
        )


def get_notification_log(limit: int = 50) -> list:
    """Fetch recent notification log entries."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM notification_log ORDER BY sent_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Backtest Results Operations ──────────────────────────────────

def insert_backtest_result(symbol: str, strategy: str, mode: str,
                           params: dict, result_json: str, computation_time: float) -> int:
    """Insert a backtest result. Returns id."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO backtest_results
               (symbol, strategy, mode, params_json, result_json, computed_at, computation_time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, strategy, mode, json.dumps(params), result_json,
             datetime.utcnow().isoformat(), computation_time),
        )
        if USE_POSTGRES:
            cursor.execute("SELECT lastval()")
            row = cursor.fetchone()
            return row["lastval"] if isinstance(row, dict) else row[0]
        return cursor.lastrowid


def get_backtest_results(symbol: str = None, strategy: str = None, limit: int = 20) -> list:
    """Fetch cached backtest results."""
    with get_db() as conn:
        conditions = []
        params = []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        query = "SELECT id, symbol, strategy, mode, params_json, computed_at, computation_time FROM backtest_results"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY computed_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d.pop("params_json", "{}"))
            result.append(d)
        return result


def get_backtest_result(result_id: int) -> Optional[dict]:
    """Fetch a single backtest result by id (includes full result_json)."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM backtest_results WHERE id = ?", (result_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json", "{}"))
        d["result"] = json.loads(d.pop("result_json", "{}"))
        return d


# ── Data Pruning (for free-tier storage limits) ──────────────────

def prune_old_data(max_candle_days: int = 14, max_signal_days: int = 14):
    """Delete candles and signals older than N days to stay within storage limits."""
    candle_cutoff = (datetime.utcnow() - timedelta(days=max_candle_days)).isoformat()
    signal_cutoff = (datetime.utcnow() - timedelta(days=max_signal_days)).isoformat()
    with get_db() as conn:
        conn.execute("DELETE FROM candles WHERE timestamp < ?", (candle_cutoff,))
        conn.execute("DELETE FROM signals WHERE timestamp < ?", (signal_cutoff,))
        conn.execute("DELETE FROM ticks WHERE timestamp < ?", (candle_cutoff,))
        conn.execute("DELETE FROM health WHERE checked_at < ?", (candle_cutoff,))
    logger.info(f"Pruned data older than {max_candle_days} days")
