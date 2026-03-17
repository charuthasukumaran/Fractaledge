"""
Backtest Report — Colored terminal output and JSON export for backtest results.
"""
import json
import sys
from dataclasses import asdict


# ── ANSI Colors ───────────────────────────────────────────────────

# Check if terminal supports color
_SUPPORTS_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

if _SUPPORTS_COLOR:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
else:
    GREEN = RED = YELLOW = CYAN = BLUE = MAGENTA = BOLD = DIM = RESET = ""


# ── Terminal Report ───────────────────────────────────────────────

def print_report(result, verbose: bool = False):
    """Print a colored terminal report of backtest results."""
    print()
    _print_header(result)
    _print_summary(result)
    _print_regime_breakdown(result)
    if verbose and result.trades:
        _print_trade_log(result)
    _print_footer(result)
    print()


def _print_header(result):
    """Print strategy name, symbol, time range, mode."""
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  BACKTEST REPORT{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")
    print()
    print(f"  {BOLD}Strategy:{RESET}  {result.strategy_name}")
    print(f"  {BOLD}Symbol:{RESET}    {result.symbol}")
    print(f"  {BOLD}Mode:{RESET}      {result.mode.upper()}")
    print(f"  {BOLD}Period:{RESET}    {result.start_time[:16]} to {result.end_time[:16]}")
    print(f"  {BOLD}Params:{RESET}    {result.params}")
    print()


def _print_summary(result):
    """Print summary statistics table."""
    print(f"  {BOLD}{CYAN}PERFORMANCE SUMMARY{RESET}")
    print(f"  {DIM}{'─' * 55}{RESET}")

    # Return color
    ret_color = GREEN if result.total_return_pct >= 0 else RED
    ret_sign = "+" if result.total_return_pct >= 0 else ""

    rows = [
        ("Initial Capital", f"\u20B9 {result.initial_capital:,.2f}"),
        ("Final Capital", f"{ret_color}\u20B9 {result.final_capital:,.2f}{RESET}"),
        ("Total Return", f"{ret_color}{ret_sign}{result.total_return_pct:.2f}%{RESET}"),
        ("", ""),
        ("Sharpe Ratio", _color_value(result.sharpe_ratio, thresholds=(0, 1, 2))),
        ("Max Drawdown", f"{RED}-{result.max_drawdown_pct:.2f}%{RESET}" if result.max_drawdown_pct > 0 else f"{GREEN}0.00%{RESET}"),
        ("DD Duration", f"{result.max_drawdown_duration} bars"),
        ("", ""),
        ("Total Trades", str(result.total_trades)),
        ("Winning", f"{GREEN}{result.winning_trades}{RESET}"),
        ("Losing", f"{RED}{result.losing_trades}{RESET}"),
        ("Win Rate", _color_value(result.win_rate, thresholds=(30, 50, 60), suffix="%")),
        ("Profit Factor", _color_value(result.profit_factor, thresholds=(0.5, 1.0, 1.5))),
        ("", ""),
        ("Avg Trade P&L", _color_pnl(result.avg_trade_pnl)),
        ("Avg Winner", f"{GREEN}\u20B9 {result.avg_winner:,.2f}{RESET}" if result.avg_winner else "-"),
        ("Avg Loser", f"{RED}\u20B9 {result.avg_loser:,.2f}{RESET}" if result.avg_loser else "-"),
        ("Largest Winner", f"{GREEN}\u20B9 {result.largest_winner:,.2f}{RESET}" if result.largest_winner else "-"),
        ("Largest Loser", f"{RED}\u20B9 {result.largest_loser:,.2f}{RESET}" if result.largest_loser else "-"),
        ("Avg Bars Held", f"{result.avg_bars_held:.1f}"),
    ]

    for label, value in rows:
        if not label:
            print()
            continue
        print(f"  {label:<22} {value}")
    print()


def _print_regime_breakdown(result):
    """Print performance by regime."""
    print(f"  {BOLD}{CYAN}REGIME BREAKDOWN{RESET}")
    print(f"  {DIM}{'─' * 55}{RESET}")

    # Header
    print(f"  {'Regime':<10} {'Trades':>8} {'Win Rate':>10} {'Avg P&L':>12} {'Total P&L':>14}")
    print(f"  {DIM}{'─' * 55}{RESET}")

    regime_colors = {"GREEN": GREEN, "AMBER": YELLOW, "RED": RED}

    for regime in ("GREEN", "AMBER", "RED"):
        data = result.regime_breakdown.get(regime, {})
        trades = data.get("trades", 0)
        win_rate = data.get("win_rate", 0)
        avg_pnl = data.get("avg_pnl", 0)
        total_pnl = data.get("total_pnl", 0)
        color = regime_colors.get(regime, "")

        wr_str = f"{win_rate:.1f}%" if trades > 0 else "-"
        avg_str = _color_pnl(avg_pnl) if trades > 0 else f"{DIM}-{RESET}"
        total_str = _color_pnl(total_pnl) if trades > 0 else f"{DIM}-{RESET}"

        print(f"  {color}{regime:<10}{RESET} {trades:>8} {wr_str:>10} {avg_str:>22} {total_str:>24}")

    print()


def _print_trade_log(result):
    """Print trade-by-trade log."""
    print(f"  {BOLD}{CYAN}TRADE LOG{RESET}")
    print(f"  {DIM}{'─' * 68}{RESET}")

    # Header
    print(f"  {'#':>3} {'Dir':<6} {'Entry':>10} {'Exit':>10} {'Bars':>5} "
          f"{'P&L':>10} {'P&L%':>8} {'Regime':>8} {'Exit Reason':<14}")
    print(f"  {DIM}{'─' * 68}{RESET}")

    for i, t in enumerate(result.trades, 1):
        dir_color = GREEN if t["direction"] == "LONG" else RED
        pnl_color = GREEN if t["pnl"] >= 0 else RED
        pnl_sign = "+" if t["pnl"] >= 0 else ""

        print(f"  {i:>3} {dir_color}{t['direction']:<6}{RESET} "
              f"{t['entry_price']:>10.2f} {t['exit_price']:>10.2f} "
              f"{t['bars_held']:>5} "
              f"{pnl_color}{pnl_sign}{t['pnl']:>9.2f}{RESET} "
              f"{pnl_color}{pnl_sign}{t['pnl_pct']:>7.2f}%{RESET} "
              f"{t['regime_at_entry']:>8} "
              f"{t['exit_reason']:<14}")

    print()


def _print_footer(result):
    """Print computation info."""
    print(f"  {DIM}{'─' * 55}{RESET}")
    print(f"  {DIM}Signals evaluated: {result.signals_evaluated} | "
          f"Computation time: {result.computation_time_seconds:.2f}s | "
          f"Mode: {result.mode}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")


# ── Helper Functions ──────────────────────────────────────────────

def _color_value(value, thresholds=(0, 1, 2), suffix=""):
    """Color a value based on thresholds (bad, neutral, good)."""
    bad, neutral, good = thresholds
    if value >= good:
        return f"{GREEN}{value:.2f}{suffix}{RESET}"
    elif value >= neutral:
        return f"{YELLOW}{value:.2f}{suffix}{RESET}"
    else:
        return f"{RED}{value:.2f}{suffix}{RESET}"


def _color_pnl(value):
    """Color a P&L value green (positive) or red (negative)."""
    if value >= 0:
        return f"{GREEN}+\u20B9 {value:,.2f}{RESET}"
    else:
        return f"{RED}\u20B9 {value:,.2f}{RESET}"


# ── JSON Export ───────────────────────────────────────────────────

def to_json(result) -> dict:
    """
    Convert BacktestResult to a JSON-serializable dict.
    Suitable for API response or file export.
    """
    try:
        return asdict(result)
    except Exception:
        # Manual conversion fallback
        return {
            "strategy_name": result.strategy_name,
            "symbol": result.symbol,
            "mode": result.mode,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return_pct": result.total_return_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "max_drawdown_duration": result.max_drawdown_duration,
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "avg_trade_pnl": result.avg_trade_pnl,
            "avg_winner": result.avg_winner,
            "avg_loser": result.avg_loser,
            "largest_winner": result.largest_winner,
            "largest_loser": result.largest_loser,
            "avg_bars_held": result.avg_bars_held,
            "trades": result.trades,
            "equity_curve": result.equity_curve[:100],  # truncate for API response
            "regime_breakdown": result.regime_breakdown,
            "signals_evaluated": result.signals_evaluated,
            "computation_time_seconds": result.computation_time_seconds,
            "params": result.params,
        }


def to_json_file(result, filepath: str):
    """Write backtest result to a JSON file."""
    data = to_json(result)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
