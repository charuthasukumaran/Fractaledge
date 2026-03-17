"""
Notification Engine — Sends alerts via Telegram and Email.
Uses only stdlib: urllib.request for Telegram, smtplib for email.
No external dependencies required.
"""
import json
import time
import logging
import urllib.request
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# ── Rate Limiting ─────────────────────────────────────────────────
# Track last send time per (alert_type, symbol) to prevent spam
_last_sent: dict[tuple, float] = {}

# Severity ranking for filtering
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

# Emoji mapping for alert types
ALERT_EMOJI = {
    "regime_change": "\U0001F6A8",    # rotating light
    "stress_spike": "\U0001F4C8",     # chart increasing
    "breakout_detected": "\U0001F4A5",  # collision
    "coupling_divergence": "\U0001F517",  # link
}

SEVERITY_EMOJI = {
    "critical": "\U0001F534",   # red circle
    "warning": "\U0001F7E0",    # orange circle
    "info": "\U0001F535",       # blue circle
}


# ── Filtering ─────────────────────────────────────────────────────

def should_send(alert_type: str, symbol: str, severity: str) -> bool:
    """
    Check if notification should be sent based on:
    1. Severity >= min_severity threshold
    2. Cooldown period hasn't elapsed for this (alert_type, symbol)
    3. Not in quiet hours (unless critical)
    """
    nc = config.notifications

    # Check severity threshold
    min_rank = SEVERITY_RANK.get(nc.min_severity, 1)
    alert_rank = SEVERITY_RANK.get(severity, 0)
    if alert_rank < min_rank:
        logger.debug(f"Notification skipped: severity {severity} < min {nc.min_severity}")
        return False

    # Check cooldown
    key = (alert_type, symbol)
    now = time.time()
    last = _last_sent.get(key, 0)
    if now - last < nc.cooldown_seconds:
        remaining = int(nc.cooldown_seconds - (now - last))
        logger.debug(f"Notification skipped: cooldown ({remaining}s remaining) for {key}")
        return False

    # Check quiet hours (skip for critical alerts)
    if severity != "critical":
        current_hour = datetime.now().hour
        if nc.quiet_hours_start > nc.quiet_hours_end:
            # Wraps midnight (e.g., 22-7)
            in_quiet = current_hour >= nc.quiet_hours_start or current_hour < nc.quiet_hours_end
        else:
            in_quiet = nc.quiet_hours_start <= current_hour < nc.quiet_hours_end
        if in_quiet:
            logger.debug(f"Notification skipped: quiet hours ({nc.quiet_hours_start}-{nc.quiet_hours_end})")
            return False

    return True


# ── Telegram ──────────────────────────────────────────────────────

def format_telegram_message(alert: dict, symbol: str) -> str:
    """Format alert as a Telegram message with emoji indicators."""
    alert_type = alert.get("type", "unknown")
    severity = alert.get("severity", "info")
    message = alert.get("message", "No details available")

    emoji = ALERT_EMOJI.get(alert_type, "\U0001F514")  # bell fallback
    sev_emoji = SEVERITY_EMOJI.get(severity, "\u2139\ufe0f")
    sev_label = severity.upper()

    # Format the type name nicely
    type_label = alert_type.replace("_", " ").title()

    now = datetime.now().strftime("%Y-%m-%d %H:%M IST")

    lines = [
        f"{emoji} *{sev_label} ALERT* {sev_emoji}",
        f"*{type_label}* — `{symbol}`",
        "",
        message,
        "",
        f"\U0001F552 {now}",
        "\U00002500" * 20,
        "_FractalEdge_",
    ]
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """
    Send a message via Telegram Bot API.
    Uses urllib.request (stdlib) — no external dependencies.
    """
    cfg = config.notifications.telegram
    if not cfg.is_configured():
        logger.debug("Telegram not configured, skipping")
        return False

    if not cfg.enabled:
        logger.debug("Telegram disabled, skipping")
        return False

    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": cfg.chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram notification sent successfully")
                return True
            else:
                logger.warning(f"Telegram API returned status {resp.status}")
                return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ── Email ─────────────────────────────────────────────────────────

def format_email_html(alert: dict, symbol: str) -> tuple:
    """
    Format alert as an HTML email with colored indicators.
    Returns (subject, html_body).
    """
    alert_type = alert.get("type", "unknown")
    severity = alert.get("severity", "info")
    message = alert.get("message", "No details available")
    type_label = alert_type.replace("_", " ").title()
    now = datetime.now().strftime("%Y-%m-%d %H:%M IST")

    # Color based on severity
    colors = {
        "critical": ("#dc2626", "#fef2f2", "#991b1b"),
        "warning": ("#f59e0b", "#fffbeb", "#92400e"),
        "info": ("#3b82f6", "#eff6ff", "#1e40af"),
    }
    accent, bg, text_dark = colors.get(severity, colors["info"])

    subject = f"[{severity.upper()}] {type_label} - {symbol} | FractalEdge"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin:0; padding:20px; font-family:'Segoe UI',Arial,sans-serif; background:#f8fafc;">
      <div style="max-width:600px; margin:0 auto; background:white; border-radius:12px; overflow:hidden; box-shadow:0 4px 6px rgba(0,0,0,0.1);">

        <!-- Header -->
        <div style="background:{accent}; padding:20px 24px; color:white;">
          <h1 style="margin:0; font-size:20px;">{ALERT_EMOJI.get(alert_type, '')} {type_label}</h1>
          <p style="margin:6px 0 0; opacity:0.9; font-size:14px;">{symbol} | {severity.upper()}</p>
        </div>

        <!-- Body -->
        <div style="padding:24px;">
          <div style="background:{bg}; border-left:4px solid {accent}; padding:16px; border-radius:6px; margin-bottom:20px;">
            <p style="margin:0; color:{text_dark}; font-size:15px; line-height:1.5;">{message}</p>
          </div>

          <table style="width:100%; font-size:13px; color:#64748b;">
            <tr>
              <td style="padding:4px 0;"><strong>Symbol:</strong> {symbol}</td>
              <td style="padding:4px 0;"><strong>Time:</strong> {now}</td>
            </tr>
            <tr>
              <td style="padding:4px 0;"><strong>Alert Type:</strong> {type_label}</td>
              <td style="padding:4px 0;"><strong>Severity:</strong> {severity.upper()}</td>
            </tr>
          </table>
        </div>

        <!-- Footer -->
        <div style="background:#f1f5f9; padding:16px 24px; text-align:center; font-size:12px; color:#94a3b8;">
          FractalEdge | Fractal Market Analysis
        </div>
      </div>
    </body>
    </html>
    """

    return subject, html_body


def send_email(subject: str, html_body: str) -> bool:
    """
    Send an HTML email via SMTP (TLS).
    Uses Python's smtplib + email.mime — no external dependencies.
    """
    cfg = config.notifications.email
    if not cfg.is_configured():
        logger.debug("Email not configured, skipping")
        return False

    if not cfg.enabled:
        logger.debug("Email disabled, skipping")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.recipient
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
            if cfg.use_tls:
                server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.sendmail(cfg.smtp_user, cfg.recipient, msg.as_string())
        logger.info("Email notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


# ── Dispatch ──────────────────────────────────────────────────────

def send_notification(alert: dict, symbol: str) -> dict:
    """
    Dispatch a single alert to all enabled channels.

    Args:
        alert: dict with keys: type, severity, message
        symbol: stock symbol that triggered the alert

    Returns:
        {"alert_type": str, "telegram": bool, "email": bool, "skipped_reason": str|None}
    """
    result = {
        "alert_type": alert.get("type", "unknown"),
        "symbol": symbol,
        "telegram": False,
        "email": False,
        "skipped_reason": None,
    }

    # Telegram
    try:
        tg_msg = format_telegram_message(alert, symbol)
        result["telegram"] = send_telegram(tg_msg)
    except Exception as e:
        logger.error(f"Telegram notification error: {e}")

    # Email
    try:
        subject, html = format_email_html(alert, symbol)
        result["email"] = send_email(subject, html)
    except Exception as e:
        logger.error(f"Email notification error: {e}")

    # Update cooldown tracker
    key = (alert.get("type", "unknown"), symbol)
    _last_sent[key] = time.time()

    # Log to database
    try:
        from database import log_notification
        alert_id = alert.get("id")
        if result["telegram"]:
            log_notification(alert_id, "telegram", "sent")
        elif config.notifications.telegram.is_configured() and config.notifications.telegram.enabled:
            log_notification(alert_id, "telegram", "failed")

        if result["email"]:
            log_notification(alert_id, "email", "sent")
        elif config.notifications.email.is_configured() and config.notifications.email.enabled:
            log_notification(alert_id, "email", "failed")
    except Exception as e:
        logger.debug(f"Notification log DB error (non-critical): {e}")

    return result


def dispatch_alerts(triggered: list, symbol: str) -> list:
    """
    Main entry point — called from workers.py after check_alerts().
    Iterates through triggered alerts, applies filtering, sends notifications.

    Args:
        triggered: list of alert dicts from alert_engine.check_alerts()
        symbol: stock symbol

    Returns:
        list of send result dicts
    """
    if not triggered:
        return []

    # Quick check: are any channels configured at all?
    nc = config.notifications
    if not (nc.telegram.is_configured() or nc.email.is_configured()):
        logger.debug("No notification channels configured, skipping dispatch")
        return []

    results = []
    for alert in triggered:
        severity = alert.get("severity", "info")
        alert_type = alert.get("type", "unknown")

        if should_send(alert_type, symbol, severity):
            result = send_notification(alert, symbol)
            results.append(result)
            sent_channels = []
            if result["telegram"]:
                sent_channels.append("telegram")
            if result["email"]:
                sent_channels.append("email")
            if sent_channels:
                logger.info(f"Notification sent via {', '.join(sent_channels)}: {alert_type} ({severity})")
        else:
            logger.debug(f"Notification filtered: {alert_type} ({severity}) for {symbol}")

    return results


# ── Test Notification ─────────────────────────────────────────────

def send_test_notification(channel: str = "all") -> dict:
    """
    Send a test notification to verify configuration.
    Used by the /notifications/test API endpoint.

    Args:
        channel: "telegram", "email", or "all"

    Returns:
        {"telegram": bool|None, "email": bool|None, "message": str}
    """
    test_alert = {
        "type": "test",
        "severity": "info",
        "message": "This is a test notification from FractalEdge. If you see this, notifications are working correctly!",
    }
    test_symbol = "TEST"

    result = {"telegram": None, "email": None, "message": ""}

    if channel in ("telegram", "all"):
        if config.notifications.telegram.is_configured():
            tg_msg = format_telegram_message(test_alert, test_symbol)
            result["telegram"] = send_telegram(tg_msg)
        else:
            result["telegram"] = False
            result["message"] += "Telegram not configured. "

    if channel in ("email", "all"):
        if config.notifications.email.is_configured():
            subject, html = format_email_html(test_alert, test_symbol)
            result["email"] = send_email(subject, html)
        else:
            result["email"] = False
            result["message"] += "Email not configured. "

    # Build summary message
    successes = []
    if result["telegram"] is True:
        successes.append("Telegram")
    if result["email"] is True:
        successes.append("Email")

    if successes:
        result["message"] = f"Test notification sent via: {', '.join(successes)}"
    elif not result["message"]:
        result["message"] = "No notifications were sent. Check your configuration."

    return result
