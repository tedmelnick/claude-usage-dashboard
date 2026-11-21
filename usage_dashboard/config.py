"""Paths, default settings, and loading/saving config.json."""
import json
from pathlib import Path

# The project folder (the one holding claude_usage_dashboard.py and config.json).
APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
DUMP_PATH = APP_DIR / "dump.json"
HISTORY_DB_PATH = APP_DIR / "usage_history.db"

# The user agent the app shipped with before it learned to detect Claude Code's version.
# A saved config that still has it is treated as "auto".
OLD_DEFAULT_UA = "claude-code/2.1.71"
FALLBACK_UA = "claude-code/2.1.71"

HISTORY_DAY_CHOICES = (7, 14, 30)

DEFAULT_CONFIG = {
    "refresh_seconds": 60,
    "stale_after_seconds": 300,
    "always_on_top": True,
    "open_folder": str(Path.home()),
    # "auto" reads the installed Claude Code version (claude --version). Anything else
    # is sent as written, e.g. "claude-code/2.1.71".
    "user_agent": "auto",
    # Off by default: refreshing rewrites Claude Code's credentials file.
    "auto_refresh_token": False,
    # Optional, fill in to show estimated cost. USD per million tokens, by model
    # family, e.g. {"opus": {"in": 4, "out": 20, "cache_write": 5, "cache_read": 0.2}}
    # See prices.example.json.
    "prices_per_mtok": {},
    "show_zero_sources": False,
    # Look: theme is "dark" or "light". window_mode is "solid", "glass"
    # (see-through window, opacity = glass_alpha) or "none" (no background and no
    # title bar, only the text and bars; Windows only). Any theme works with any mode.
    "theme": "dark",
    "window_mode": "solid",
    "glass_alpha": 0.82,
    # Reset times: false = 12-hour clock (3:10 PM), true = 24-hour clock (15:10).
    "clock_24h": False,
    "window_x": None,
    "window_y": None,
    # Compact view: only the plan-limit bars, in a small window.
    "compact": False,
    # Claude Code history: days shown (7, 14 or 30) and how many top projects to list.
    "history_days": 7,
    "top_projects": 5,
    # Alerts (toast notification + a strip in the window).
    "alerts_enabled": True,
    "alert_thresholds": [80, 90],   # percent; reaching 100 always alerts
    "alert_on_runout": True,        # "on pace to run out before the reset"
    "alert_on_reset": True,         # a limit that was well used has reset
    "alert_reset_min_pct": 50,      # only announce a reset if the limit had reached this
    "alert_sound": False,           # also play the system sound (Windows)
    # Snapshots: save each limit reading to usage_history.db so past weeks can be compared.
    "snapshots_enabled": True,
    "snapshot_keep_days": 365,
    "trend_weeks": 8,
    # Taskbar: the icon redraws itself as two bars (session, weekly), and the taskbar
    # button shows a progress strip for whichever limit is more urgent (Windows).
    "dynamic_icon": True,
    "taskbar_progress": True,
    # Updates: look for a newer GitHub release at start and every few hours, and show a
    # button when one exists. Nothing is installed without you pressing Update now.
    "check_for_updates": True,
    "skipped_version": "",
}


def _clean(cfg):
    if cfg.get("history_days") not in HISTORY_DAY_CHOICES:
        cfg["history_days"] = 7
    try:
        cfg["top_projects"] = max(0, min(15, int(cfg.get("top_projects", 5))))
        cfg["trend_weeks"] = max(2, min(12, int(cfg.get("trend_weeks", 8))))
    except (TypeError, ValueError):
        cfg["top_projects"], cfg["trend_weeks"] = 5, 8
    th = cfg.get("alert_thresholds")
    if not isinstance(th, list):
        th = DEFAULT_CONFIG["alert_thresholds"]
    clean = []
    for t in th:
        try:
            clean.append(int(t))
        except (TypeError, ValueError):
            pass
    cfg["alert_thresholds"] = sorted(set(t for t in clean if 0 < t < 100))
    return cfg


def load_config(path=None):
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return _clean(cfg)


def save_config(cfg, path=None):
    try:
        Path(path or CONFIG_PATH).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass
