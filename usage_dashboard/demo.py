"""Fake data for previewing the look (python claude_usage_dashboard.py --demo)."""
import time
from datetime import date, timedelta


def demo_usage(now, session=42, weekly=68):
    return {"limits": [
        {"kind": "session", "percent": session, "resets_at": (now + timedelta(minutes=157)).isoformat()},
        {"kind": "weekly_all", "percent": weekly, "resets_at": (now + timedelta(hours=107)).isoformat()},
        {"kind": "weekly_scoped", "percent": 55, "resets_at": (now + timedelta(hours=107)).isoformat(),
         "scope": {"model": {"display_name": "Sonnet"}}},
        {"kind": "weekly_scoped", "percent": 31, "resets_at": (now + timedelta(hours=107)).isoformat(),
         "scope": {"model": {"display_name": "Opus"}}},
        {"kind": "weekly_scoped", "percent": 12, "resets_at": (now + timedelta(hours=107)).isoformat(),
         "scope": {"model": {"display_name": "Fable"}}},
    ], "seven_day_breakdown": {"rows": [
        {"key": "claude_code", "display_name": "Claude Code", "percent": 20, "percent_of_limit": 14},
        {"key": "chat", "display_name": "Chats", "percent": 10, "percent_of_limit": 7},
        {"key": "cowork", "display_name": "Cowork", "percent": 60, "percent_of_limit": 41},
        {"key": "claude_duet", "display_name": "Claude Duet", "percent": 10, "percent_of_limit": 6},
    ]}}


def demo_history(days=7):
    today = date.today()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    base = [(3.2, 1.1, 0.2), (5.0, 2.0, 0.4), (2.1, 0.9, 0.1), (6.4, 3.0, 0.8), (4.1, 1.5, 0.3),
            (5.5, 2.2, 0.5), (3.0, 1.9, 0.9)]
    by_day = {}
    for i, d in enumerate(day_list):
        s, o, f = base[i % len(base)]
        by_day[d] = {"sonnet": [int(s * 1e6), 0, 0, 0], "opus": [int(o * 1e6), 0, 0, 0],
                     "fable": [int(f * 1e6), 0, 0, 0]}
    scale = days / 7.0
    by_project = {
        "claude-usage-dashboard": {"opus": [int(9e6 * scale), 0, 0, 0], "sonnet": [int(4e6 * scale), 0, 0, 0]},
        "mobile-app": {"sonnet": [int(7e6 * scale), 0, 0, 0]},
        "data-pipeline": {"opus": [int(3.5e6 * scale), 0, 0, 0], "fable": [int(1.2e6 * scale), 0, 0, 0]},
        "blog-engine": {"sonnet": [int(2.1e6 * scale), 0, 0, 0]},
        "scratch": {"haiku": [int(0.6e6 * scale), 0, 0, 0]},
    }
    return {"days": day_list, "by_day": by_day, "by_project": by_project}


def demo_trend(weeks=8):
    """Past weekly peaks (all models), oldest first; the last one is the open window."""
    peaks = [54, 71, 88, 63, 97, 76, 82, 68, 59, 91, 73, 66]
    now = time.time()
    week = 7 * 86400
    out = []
    for i in range(weeks):
        offset = weeks - 1 - i
        out.append({"resets_at": now + 107 * 3600 - offset * week,
                    "peak": peaks[i % len(peaks)] if offset else 68, "current": offset == 0})
    return out


def demo_releases():
    """Pretend newer releases, for previewing the update button (--demo-update)."""
    return [
        {"tag": "v2.5.0", "date": "2026-11-28",
         "notes": "New\n• A made-up feature, shown only in this preview\n• Another made-up change"},
        {"tag": "v2.4.1", "date": "2026-11-24",
         "notes": "Fixes\n• A made-up fix, shown only in this preview"},
    ]


# --demo-eggs walks the session bar through these readings, one per poll, so the number
# rules can be watched without waiting for real usage.
DEMO_EGG_SESSION = [20, 24, 28, 32, 36, 41, 30, 35, 40, 45, 47, 42, 49, 52, 69, 80, 90, 12]


def demo_stats():
    return {"rows": 400, "first_day": "2026-09-14", "longest_streak": 23, "current_streak": 6,
            "busiest": {"day": "2026-10-27", "points": 31.0},
            "top_source": {"name": "Cowork", "points": 412.0},
            "top_project": {"name": "claude-usage-dashboard", "tokens": 48_500_000}}
