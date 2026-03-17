"""
Configuration for Fractal Stock Analyzer
-----------------------------------------
Supports Yahoo Finance (free) and Angel One SmartAPI (real-time).
"""
import os
from dataclasses import dataclass, field


@dataclass
class MFDFAConfig:
    window_size: int = 1000
    min_bars: int = 200
    q_orders: list = field(default_factory=lambda: [-5, -3, -1, 0, 1, 2, 3, 5])
    scale_min: int = 10
    scale_max: int = 250
    num_scales: int = 20
    stress_green_max: float = 0.35
    stress_amber_max: float = 0.65


@dataclass
class SmartAPIConfig:
    """Angel One SmartAPI credentials — set via environment variables."""
    api_key: str = os.getenv("ANGEL_API_KEY", "")
    client_id: str = os.getenv("ANGEL_CLIENT_ID", "")
    password: str = os.getenv("ANGEL_PIN", "")
    totp_secret: str = os.getenv("ANGEL_TOTP_SECRET", "")
    # Rate limiting
    requests_per_second: float = 5.0
    max_retries: int = 3
    retry_backoff_base: float = 2.0

    def is_configured(self) -> bool:
        """Check if all required credentials are set."""
        return all([self.api_key, self.client_id, self.password, self.totp_secret])


@dataclass
class TelegramConfig:
    """Telegram Bot API — set via environment variables."""
    enabled: bool = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class EmailConfig:
    """SMTP Email — set via environment variables."""
    enabled: bool = os.getenv("EMAIL_ENABLED", "true").lower() == "true"
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    recipient: str = os.getenv("NOTIFY_EMAIL", "")
    use_tls: bool = True

    def is_configured(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.recipient)


@dataclass
class NotificationConfig:
    """Push notification settings."""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    min_severity: str = "warning"       # "info", "warning", "critical"
    cooldown_seconds: int = 300         # 5 min between same alert type per symbol
    quiet_hours_start: int = 22         # 10 PM — suppress non-critical alerts
    quiet_hours_end: int = 7            # 7 AM


@dataclass
class BacktestConfig:
    """Backtesting engine settings."""
    default_strategy: str = "regime"
    initial_capital: float = 100000.0
    commission_pct: float = 0.03        # 0.03% per trade (typical Indian broker)
    slippage_pct: float = 0.01          # 0.01% slippage
    signal_step: int = 1                # compute signal every N bars (full mode)
    warmup_bars: int = 1000             # bars before first signal
    risk_per_trade_pct: float = 1.0     # % of capital risked per trade
    max_positions: int = 1              # max concurrent positions


@dataclass
class AppConfig:
    db_path: str = os.getenv("DB_PATH", "nifty_stress.db")
    database_url: str = os.getenv("DATABASE_URL", "")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    candle_interval_minutes: int = 5
    backfill_days: int = 30
    api_host: str = "0.0.0.0"
    api_port: int = int(os.getenv("PORT", "8000"))
    mfdfa: MFDFAConfig = field(default_factory=MFDFAConfig)
    # Angel One SmartAPI
    smartapi: SmartAPIConfig = field(default_factory=SmartAPIConfig)
    # AI (Anthropic Claude) — set ANTHROPIC_API_KEY env var to enable
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = "claude-sonnet-4-20250514"
    ai_insight_interval_minutes: int = 5
    # Legacy Gemini support (fallback)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    # Push Notifications (Telegram + Email)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    # Backtesting Engine
    backtest: BacktestConfig = field(default_factory=BacktestConfig)


config = AppConfig()
