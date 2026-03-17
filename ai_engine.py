"""
AI Engine — Anthropic Claude integration for market insights and chat.
----------------------------------------------------------------------
Uses the Anthropic Python SDK (anthropic).
Provides detailed, presentable market analysis for retail traders.
Requires ANTHROPIC_API_KEY environment variable.
"""
import json
import logging
import time
from typing import Optional

from database import get_latest_signal, get_signals, get_candles

logger = logging.getLogger(__name__)

# ── Lazy-loaded Anthropic client ────────────────────────────────

_client = None


def _get_client():
    """Lazy-init the Anthropic client."""
    global _client
    if _client is not None:
        return _client

    from config import config
    if not config.anthropic_api_key:
        return None

    import anthropic
    _client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    return _client


# ── System Prompts ────────────────────────────────────────────────

INSIGHT_SYSTEM_PROMPT = """You are a friendly market analyst for the FractalEdge app. Your audience is everyday retail traders in India — people who may not know technical jargon.

Your job: Take raw market data and turn it into a DETAILED, CLEAR, and PRESENTABLE analysis report that anyone can understand.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS (use markdown):

## 🎯 Quick Summary
One bold sentence about the overall market mood right now.

## 📊 Market Mood: [GREEN/AMBER/RED]
Explain what the current regime means in simple terms. Is it calm, cautious, or dangerous? Use an everyday analogy (like weather or traffic signals).

## 📈 Trend & Momentum
- What direction is the price moving? (Up/Down/Sideways)
- Is the momentum strong or weak? Explain RSI in plain language (e.g., "Buyers are in control" or "Stock looks tired and overbought")
- MACD signal — is momentum picking up or fading?

## 🔑 Key Levels to Watch
- **Support** (floor price): List the nearest support levels and what they mean
- **Resistance** (ceiling price): List the nearest resistance levels
- Where is the price right now relative to these levels?

## ⚡ Breakout Status
- Has the price broken through any important level?
- If yes, how strong is the breakout? (volume, confidence)
- If no breakout, what would trigger one?

## 🛡️ Risk Assessment
- For someone wanting to BUY: Where to set stop-loss, what targets to aim for
- For someone wanting to SELL/SHORT: Same info
- How much of your capital should you risk? (based on regime)
- Risk-to-reward ratio explained simply

## 💡 What This Means For You
2-3 sentences of plain-English actionable guidance. NOT buy/sell recommendations, but explain the situation like you're advising a friend. For example: "Market is calm and trending up — good conditions for patient traders" or "Stress is high, be very careful with new positions."

RULES:
- Use simple, everyday language — explain like you're talking to a friend who just started trading
- When you mention a number, explain what it means (e.g., "RSI is 72, which means buyers have been pushing hard and the stock might need a breather")
- Use the actual numbers from the data (prices, levels, scores)
- Be honest — if data is mixed or unclear, say so
- NEVER give specific buy/sell/trade recommendations
- Use the emoji headers exactly as shown above for consistent formatting
- Keep each section concise but informative (2-4 sentences each)"""

CHAT_SYSTEM_PROMPT = """You are a friendly AI assistant for the FractalEdge app. You help Indian retail traders understand market conditions using data from the app.

You have access to real-time market data including:
- Stress scores and regime labels (GREEN = calm, AMBER = caution, RED = high stress)
- Multifractal analysis (MFDFA) — measures market complexity
- Hurst exponent — tells if market is trending (>0.5) or choppy (<0.5)
- Trend indicators: EMA, RSI, MACD, ATR
- Support/Resistance levels with strength scores
- Breakout detection with quality scoring
- Risk levels: stop-loss, targets, risk-reward ratios
- Market coupling (how NIFTY and BANKNIFTY move together)

GUIDELINES:
- Talk like a knowledgeable friend, not a textbook
- When someone asks "what does X mean?", explain with everyday analogies
- Use the actual data numbers when answering
- If asked about a specific indicator, explain what the current value means in plain language
- Never give specific buy/sell recommendations — instead explain what the data suggests
- Keep responses concise (2-5 sentences for simple questions, more for complex ones)
- If asked something outside market analysis, politely redirect
- Use markdown formatting for readability (bold for key points, bullet lists for clarity)
- Reference specific numbers: "Your RSI is 65, which is in the healthy zone — not overbought yet"
- When discussing risk, always mention the regime context (GREEN/AMBER/RED)"""


# ── Market Context Builder ────────────────────────────────────────

def build_market_context(symbol: str = "^NSEI") -> str:
    """Assemble current market data into a structured text block for the AI prompt."""
    parts = []

    parts.append(f"SYMBOL: {symbol}")

    # Latest signal
    signal = get_latest_signal(symbol=symbol)
    if signal:
        features = {}
        if signal.get("features_json"):
            try:
                features = json.loads(signal["features_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        parts.append(f"""CURRENT MARKET STATUS:
- Regime: {signal.get('regime_label', 'UNKNOWN')}
- Ensemble Score: {signal.get('ensemble_score', 'N/A')} (0=calm, 1=extreme stress)
- Stress Score: {signal.get('stress_score', 'N/A')}
- Coupling Score: {signal.get('coupling_score', 'N/A')} (NIFTY-BANKNIFTY correlation)
- Hurst Exponent h(2): {signal.get('hurst_exponent', 'N/A')}
- Spectral Width delta-alpha: {signal.get('spectral_width', 'N/A')}
- Timestamp: {signal.get('timestamp', 'N/A')}""")

        # Feature decomposition
        if features:
            parts.append(f"""
FEATURE BREAKDOWN:
- Spectral Width Score: {features.get('spectral_width_score', 'N/A')}
- Hurst Deviation Score: {features.get('hurst_deviation_score', 'N/A')}
- Asymmetry Score: {features.get('asymmetry_score', 'N/A')}
- H(q) Volatility Score: {features.get('hq_volatility_score', 'N/A')}""")

        # ── Trend Analysis ──────────────────────────────────────
        trend = features.get("trend", {})
        if trend:
            parts.append(f"""
TREND ANALYSIS:
- Trend: {trend.get('trend', 'N/A')} (strength: {trend.get('trend_strength', 'N/A')})
- EMA 9: {trend.get('ema_9', 'N/A')} | EMA 21: {trend.get('ema_21', 'N/A')} | EMA 50: {trend.get('ema_50', 'N/A')}
- RSI(14): {trend.get('rsi_14', 'N/A')} ({trend.get('rsi_signal', 'N/A')})
- MACD: {trend.get('macd', {}).get('macd', 'N/A')} | Signal: {trend.get('macd', {}).get('signal', 'N/A')} | Histogram: {trend.get('macd', {}).get('histogram', 'N/A')} ({trend.get('macd_signal', 'N/A')})
- ATR(14): {trend.get('atr_14', 'N/A')}""")

        # ── Support & Resistance ────────────────────────────────
        sr = features.get("support_resistance", {})
        if sr:
            sup_str = ", ".join(
                f"{s['level']} ({s['touches']} touches)" for s in sr.get("support_levels", [])[:3]
            ) or "N/A"
            res_str = ", ".join(
                f"{r['level']} ({r['touches']} touches)" for r in sr.get("resistance_levels", [])[:3]
            ) or "N/A"
            pivots = sr.get("pivots", {})
            parts.append(f"""
SUPPORT & RESISTANCE:
- Nearest Support: {sr.get('nearest_support', 'N/A')}
- Nearest Resistance: {sr.get('nearest_resistance', 'N/A')}
- Support Levels: {sup_str}
- Resistance Levels: {res_str}
- Pivot Point: {pivots.get('pp', 'N/A')} | S1: {pivots.get('s1', 'N/A')} | R1: {pivots.get('r1', 'N/A')}
- Price Position: {sr.get('price_position', 'N/A')} (0=at support, 1=at resistance)""")

        # ── Breakout Status ─────────────────────────────────────
        bo = features.get("breakout", {})
        if bo:
            if bo.get("breakout_detected"):
                parts.append(f"""
BREAKOUT STATUS:
- Breakout Detected: YES — {bo.get('direction', 'N/A')}
- Broke {bo.get('level_type', 'level')}: {bo.get('broken_level', 'N/A')}
- Volume Ratio: {bo.get('volume_ratio', 'N/A')}x average
- Body Ratio: {bo.get('body_ratio', 'N/A')}x average
- Confidence: {round(bo.get('confidence', 0) * 100)}%
- Regime Alignment: {'YES' if bo.get('regime_alignment') else 'NO'}""")
            else:
                parts.append("\nBREAKOUT STATUS: No breakout detected.")

        # ── Breakout Quality ────────────────────────────────────
        bq = features.get("breakout_quality", {})
        if bq and bq.get("quality_score", 0) > 0:
            components = bq.get("quality_components", {})
            parts.append(f"""
BREAKOUT QUALITY:
- Quality Score: {bq.get('quality_score', 'N/A')} (0-1)
- Quality Signal: {bq.get('quality_signal', 'N/A')}
- Hurst Persistence: {components.get('hurst_persistence', 'N/A')}
- Spectrum Stability: {components.get('spectrum_stability', 'N/A')}
- MFDCCA Alignment: {components.get('mfdcca_alignment', 'N/A')}
- S/R Fractal Quality: {components.get('sr_fractal_quality', 'N/A')}
- Classical Confirmation: {components.get('classical_confirmation', 'N/A')}""")

        # ── Risk Levels ─────────────────────────────────────────
        risk = features.get("risk", {})
        if risk:
            lt = risk.get("long_trade", {})
            st = risk.get("short_trade", {})
            parts.append(f"""
RISK LEVELS:
- Long Trade: SL {lt.get('stoploss', 'N/A')} ({lt.get('stoploss_pct', 'N/A')}%), T1 {lt.get('target_1', 'N/A')}, T2 {lt.get('target_2', 'N/A')}, T3 {lt.get('target_3', 'N/A')} (R:R {lt.get('risk_reward', 'N/A')})
- Short Trade: SL {st.get('stoploss', 'N/A')} ({st.get('stoploss_pct', 'N/A')}%), T1 {st.get('target_1', 'N/A')}, T2 {st.get('target_2', 'N/A')}, T3 {st.get('target_3', 'N/A')} (R:R {st.get('risk_reward', 'N/A')})
- Suggested Risk: {risk.get('suggested_risk_pct', 'N/A')}% of capital
- Note: {risk.get('regime_note', 'N/A')}""")

        # ── Topless Target ──────────────────────────────────────
        topless = features.get("topless_target", {})
        if topless and topless.get("is_topless"):
            parts.append(f"""
TOPLESS TARGET (OPEN-ENDED RUNNER):
- Is Topless: YES
- Topless Score: {topless.get('topless_score', 'N/A')}
- Price Discovery: {'YES' if topless.get('price_discovery') else 'NO'}
- All-Time High: {topless.get('all_time_high', 'N/A')}
- ATH Proximity: {topless.get('all_time_high_proximity', 'N/A')}
- Trailing Stop: {topless.get('trailing_stop', 'N/A')}
- Strategy: {topless.get('strategy', 'N/A')}""")

    # Recent regime history (last 10 signals)
    recent_signals = get_signals(limit=10, symbol=symbol)
    if recent_signals:
        regime_seq = [s.get("regime_label", "?") for s in recent_signals]
        parts.append(f"\nRECENT REGIME HISTORY (oldest to newest): {' -> '.join(regime_seq)}")

        # Score trend
        scores = [s.get("ensemble_score", 0) for s in recent_signals if s.get("ensemble_score") is not None]
        if len(scores) >= 2:
            trend_dir = "rising" if scores[-1] > scores[0] else "falling" if scores[-1] < scores[0] else "stable"
            parts.append(f"SCORE TREND: {trend_dir} (from {scores[0]:.3f} to {scores[-1]:.3f})")

    # Latest price
    candles = get_candles(limit=5, symbol=symbol)
    if candles:
        latest = candles[-1]
        parts.append(f"\nLATEST PRICE ({symbol}): {latest.get('close', 'N/A')} (as of {latest.get('timestamp', 'N/A')})")
        if len(candles) >= 2:
            prev = candles[-2]
            if prev.get("close") and latest.get("close"):
                change_pct = ((latest["close"] / prev["close"]) - 1) * 100
                parts.append(f"PRICE CHANGE: {change_pct:+.2f}% from previous candle")

    return "\n".join(parts) if parts else "No market data available yet."


# ── Insight Generation (with caching) ─────────────────────────────

_insight_cache = {}  # keyed by symbol: {"text": ..., "timestamp": ...}


def generate_insight(symbol: str = "^NSEI") -> dict:
    """
    Generate a detailed, presentable market insight using Claude.
    Results are cached per symbol for ai_insight_interval_minutes to respect rate limits.
    """
    from config import config

    # Check cache (per symbol)
    cache_ttl = config.ai_insight_interval_minutes * 60
    now = time.time()
    cached = _insight_cache.get(symbol)
    if cached and cached.get("text") and (now - cached.get("timestamp", 0)) < cache_ttl:
        return {
            "insight": cached["text"],
            "cached": True,
            "generated_at": cached["timestamp"],
            "symbol": symbol,
        }

    client = _get_client()
    if client is None:
        return {"insight": None, "error": "AI not configured. Set ANTHROPIC_API_KEY environment variable."}

    context = build_market_context(symbol=symbol)
    prompt = f"""Here is the current market data from FractalEdge for {symbol}:

{context}

Based on this data, provide a detailed market analysis report for a retail trader. Follow the format specified in your instructions exactly."""

    try:
        response = client.messages.create(
            model=config.anthropic_model,
            max_tokens=2000,
            system=INSIGHT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        insight_text = response.content[0].text.strip()

        # Update cache per symbol
        _insight_cache[symbol] = {"text": insight_text, "timestamp": now}

        logger.info(f"Generated AI insight for {symbol}: {insight_text[:80]}...")
        return {
            "insight": insight_text,
            "cached": False,
            "generated_at": now,
            "symbol": symbol,
        }

    except Exception as e:
        logger.error(f"Claude insight error: {e}")
        # Return cached version if available
        cached = _insight_cache.get(symbol)
        if cached and cached.get("text"):
            return {
                "insight": cached["text"],
                "cached": True,
                "generated_at": cached["timestamp"],
                "symbol": symbol,
                "warning": "Using cached insight due to API error.",
            }
        return {"insight": None, "error": f"AI error: {str(e)}"}


# ── Chat ──────────────────────────────────────────────────────────

def chat(user_message: str, history: Optional[list] = None, symbol: str = "^NSEI") -> dict:
    """
    Handle a chat message from the user.

    Args:
        user_message: The user's question/message
        history: List of {"role": "user"|"assistant", "text": "..."} dicts
        symbol: Stock symbol for market context

    Returns:
        {"reply": "...", "context_used": True/False}
    """
    client = _get_client()
    if client is None:
        return {"reply": "AI is not configured. Please set the ANTHROPIC_API_KEY environment variable and restart the server.", "context_used": False}

    from config import config

    # Build market context
    context = build_market_context(symbol=symbol)

    # Build Claude messages
    messages = []

    # Add chat history (last 10 messages)
    if history:
        for msg in history[-10:]:
            role = msg.get("role", "user")
            text = msg.get("text", "")
            # Normalize role names
            if role == "model":
                role = "assistant"
            if role in ("user", "assistant") and text:
                messages.append({"role": role, "content": text})

    # Add current message with market context
    full_prompt = f"""CURRENT MARKET DATA for {symbol} (use this to answer the trader's question):
{context}

TRADER'S QUESTION: {user_message}"""

    messages.append({"role": "user", "content": full_prompt})

    try:
        response = client.messages.create(
            model=config.anthropic_model,
            max_tokens=1500,
            system=CHAT_SYSTEM_PROMPT,
            messages=messages,
        )
        reply_text = response.content[0].text.strip()

        return {"reply": reply_text, "context_used": True}

    except Exception as e:
        logger.error(f"Claude chat error: {e}")
        return {"reply": f"Sorry, I encountered an error. Please try again in a moment. ({str(e)})", "context_used": False}


# ── Trade Plan Generator ──────────────────────────────────────────

TRADE_PLAN_SYSTEM_PROMPT = """You are an expert trade plan generator for the FractalEdge app.
Given comprehensive market data, generate a structured trade plan that a retail trader can follow.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS (use markdown):

## Trade Plan: {SYMBOL}

### Setup
Describe the current market setup in 2-3 sentences. What pattern is forming? What does the fractal analysis say?

### Direction
State clearly: **LONG**, **SHORT**, or **NO TRADE** with a 1-sentence reason.

### Entry
- **Entry Zone:** Specific price range
- **Trigger:** What event should trigger entry (e.g., "Enter if price holds above 22,500 for 2 candles")

### Stop-Loss
- **Level:** Exact price from the risk data
- **Distance:** X% from entry
- **Reason:** Based on what (ATR, S/R level, fractal validation)

### Targets
| Target | Price | Risk:Reward | Action |
|--------|-------|-------------|--------|
| T1 | price | 1:1 | Book 33% profits |
| T2 | price | 1:2 | Book 33% profits |
| T3 | price | 1:3 | Exit remaining |

### Position Sizing
- **Regime:** GREEN/AMBER/RED
- **Suggested Risk:** X% of capital
- **For 1 Lakh account:** X shares at entry
- **For 5 Lakh account:** X shares at entry

### Conditions to Abort
- List 2-3 specific conditions that would invalidate this plan
- Use actual price levels and indicator values

### Risk Warning
This is for educational purposes only. Always do your own research. Past patterns do not guarantee future results.

RULES:
- Use ACTUAL prices and levels from the data — never make up numbers
- Position sizing MUST adapt to regime (smaller in RED, normal in GREEN)
- Be specific with price levels — use exact numbers
- Include the regime-based risk adjustment
- Keep it practical — a common trader should be able to follow this
- NEVER say "buy this stock" — frame it as "if you decide to trade..."
- If the data is mixed or unclear, recommend NO TRADE with explanation"""


def generate_trade_plan(symbol: str = "^NSEI") -> dict:
    """Generate a structured trade plan using Claude."""
    client = _get_client()
    if client is None:
        return {"trade_plan": None, "error": "AI not configured."}

    from config import config
    context = build_market_context(symbol=symbol)

    prompt = f"""Generate a detailed trade plan for {symbol} based on this data:

{context}

Follow the format in your instructions. Use actual price levels from the data."""

    try:
        response = client.messages.create(
            model=config.anthropic_model,
            max_tokens=2000,
            system=TRADE_PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        plan_text = response.content[0].text.strip()
        return {"trade_plan": plan_text, "cached": False, "symbol": symbol}
    except Exception as e:
        logger.error(f"Trade plan error: {e}")
        return {"trade_plan": None, "error": str(e)}


def is_configured() -> bool:
    """Check if AI is configured (Anthropic API key is set)."""
    from config import config
    return bool(config.anthropic_api_key)
