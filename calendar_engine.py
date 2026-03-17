"""
Economic Calendar — major Indian and global market events.
Hardcoded for reliability (no external API dependency).
"""
from datetime import datetime, date, timedelta


def _generate_fo_expiries(year: int) -> list:
    """Generate monthly F&O expiry dates (last Thursday of each month)."""
    expiries = []
    for month in range(1, 13):
        # Find last Thursday
        if month == 12:
            last_day = date(year, 12, 31)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        while last_day.weekday() != 3:  # 3 = Thursday
            last_day -= timedelta(days=1)
        expiries.append({
            "date": last_day.isoformat(),
            "event": f"F&O Monthly Expiry",
            "category": "expiry",
            "impact": "high",
        })
    return expiries


# Fixed events (updated periodically)
FIXED_EVENTS = [
    # RBI Policy meetings 2025-2026 (bimonthly)
    {"date": "2025-04-09", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2025-06-06", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2025-08-08", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2025-10-10", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2025-12-05", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2026-02-06", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2026-04-08", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},
    {"date": "2026-06-05", "event": "RBI Monetary Policy", "category": "central_bank", "impact": "high"},

    # US Fed meetings 2025-2026
    {"date": "2025-03-19", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-05-07", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-06-18", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-07-30", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-09-17", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-10-29", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2025-12-10", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2026-01-28", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},
    {"date": "2026-03-18", "event": "US Fed Rate Decision", "category": "global", "impact": "high"},

    # Indian market holidays 2025
    {"date": "2025-03-14", "event": "Holi — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-03-31", "event": "Eid ul-Fitr — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-04-14", "event": "Dr. Ambedkar Jayanti — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-04-18", "event": "Good Friday — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-05-01", "event": "Maharashtra Day — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-08-15", "event": "Independence Day — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-10-02", "event": "Gandhi Jayanti — Market Closed", "category": "holiday", "impact": "medium"},
    {"date": "2025-11-05", "event": "Diwali — Market Closed", "category": "holiday", "impact": "medium"},

    # Quarterly results seasons
    {"date": "2025-04-15", "event": "Q4 Results Season Begins", "category": "earnings", "impact": "medium"},
    {"date": "2025-07-15", "event": "Q1 Results Season Begins", "category": "earnings", "impact": "medium"},
    {"date": "2025-10-15", "event": "Q2 Results Season Begins", "category": "earnings", "impact": "medium"},
    {"date": "2026-01-15", "event": "Q3 Results Season Begins", "category": "earnings", "impact": "medium"},
]


def get_upcoming_events(days_ahead: int = 30) -> dict:
    """Return events within the next N days."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    # Combine fixed events with generated expiries
    all_events = FIXED_EVENTS.copy()
    all_events.extend(_generate_fo_expiries(today.year))
    if today.month >= 10:
        all_events.extend(_generate_fo_expiries(today.year + 1))

    upcoming = []
    for evt in all_events:
        try:
            evt_date = date.fromisoformat(evt["date"])
        except (ValueError, KeyError):
            continue

        if today <= evt_date <= cutoff:
            days_until = (evt_date - today).days
            upcoming.append({
                **evt,
                "days_until": days_until,
                "day_of_week": evt_date.strftime("%A"),
                "is_today": days_until == 0,
                "is_tomorrow": days_until == 1,
            })

    upcoming.sort(key=lambda x: x["date"])

    return {
        "events": upcoming,
        "count": len(upcoming),
        "range_days": days_ahead,
        "as_of": today.isoformat(),
    }
