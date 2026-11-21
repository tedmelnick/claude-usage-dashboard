#!/usr/bin/env python3
"""Claude Usage Dashboard: an always-on-top window for a second monitor.

Shows plan limits (session, weekly, and every per-model weekly quota your account
reports), a projection of whether you will hit each limit before it resets, and
Claude Code token history by model. Standard library only (tkinter).

Data sources
  * Plan limits: Anthropic's unofficial /api/oauth/usage endpoint, the same one
    Claude Code's /usage command reads. Uses the sign-in token Claude Code saves
    in ~/.claude/.credentials.json. This endpoint is unofficial and may change.
  * History: Claude Code's local logs in ~/.claude/projects/**/*.jsonl.

Run:  pythonw claude_usage_dashboard.py          (no console window)
      python  claude_usage_dashboard.py --demo   (fake data, to preview the look)
      python  claude_usage_dashboard.py --dump   (save the raw API reply to dump.json)
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DUMP_PATH = APP_DIR / "dump.json"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code's public OAuth client

DEFAULT_CONFIG = {
    "refresh_seconds": 60,
    "stale_after_seconds": 300,
    "always_on_top": True,
    "open_folder": str(Path.home()),
    "user_agent": "claude-code/2.1.71",
    # Off by default: refreshing rewrites Claude Code's credentials file.
    "auto_refresh_token": False,
    # Optional, fill in to show estimated cost. USD per million tokens, by model
    # family, e.g. {"opus": {"in": 5, "out": 25, "cache_write": 6.25, "cache_read": 0.5}}
    "prices_per_mtok": {},
    "show_zero_sources": False,
    # Look: theme is "dark" or "light". window_mode is "solid", "glass"
    # (see-through window, opacity = glass_alpha) or "none" (no background and no
    # title bar, only the text and bars). Any theme works with any window_mode.
    "theme": "dark",
    "window_mode": "solid",
    "glass_alpha": 0.82,
    # Reset times: false = 12-hour clock (3:10 PM), true = 24-hour clock (15:10).
    "clock_24h": False,
    "window_x": None,
    "window_y": None,
}

WINDOW_MIN = {"session": 300, "weekly": 10080}

# Themes. The names below are module globals that apply_palette() swaps at run time;
# the values here are the dark theme (the default).
BG = "#1c1c1b"
PANEL = "#242423"
TRACK = "#2c2c2a"
BORDER = "#3a3a38"
TEXT = "#ececea"
TEXT2 = "#b8b7b0"
MUTED = "#9a9992"
BLUE, BLUE_D = "#378ADD", "#0C447C"
RED, RED_D = "#E24B4A", "#501313"
AMBER, AMBER_D = "#EF9F27", "#412402"
GREEN, GREEN_D = "#639922", "#173404"
TEAL = "#1D9E75"
BTN_BORDER = "#55544f"
BANNER_FG, BANNER_BG = "#FAC775", "#412402"
CHIP = {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
        "over": ("#F7C1C1", "#501313")}

PALETTES = {
    "dark": {
        "BG": "#1c1c1b", "PANEL": "#242423", "TRACK": "#2c2c2a", "BORDER": "#3a3a38",
        "TEXT": "#ececea", "TEXT2": "#b8b7b0", "MUTED": "#9a9992",
        "BLUE_D": "#0C447C", "RED_D": "#501313", "AMBER_D": "#412402", "GREEN_D": "#173404",
        "BTN_BORDER": "#55544f", "BANNER_FG": "#FAC775", "BANNER_BG": "#412402",
        "CHIP": {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
                 "over": ("#F7C1C1", "#501313")},
    },
    "light": {
        "BG": "#f6f5f1", "PANEL": "#ffffff", "TRACK": "#e4e3dc", "BORDER": "#d3d2ca",
        "TEXT": "#1f1f1d", "TEXT2": "#4a4944", "MUTED": "#6b6a63",
        "BLUE_D": "#B5D4F4", "RED_D": "#F7C1C1", "AMBER_D": "#FAC775", "GREEN_D": "#C0DD97",
        "BTN_BORDER": "#b5b4ac", "BANNER_FG": "#633806", "BANNER_BG": "#FAEEDA",
        "CHIP": {"ok": ("#27500A", "#EAF3DE"), "warn": ("#633806", "#FAEEDA"),
                 "over": ("#791F1F", "#FCEBEB")},
    },
}
# "No background" makes this one colour see-through (Windows). It is a hair off the
# theme's own background so the text edges blend into it.
TRANSPARENT_KEY = {"dark": "#010101", "light": "#fefefe"}
WINDOW_MODES = ["solid", "glass", "none"]
WINDOW_MODE_LABEL = {"solid": "Solid", "glass": "See-through", "none": "No background"}


def apply_palette(theme, mode):
    """Point the colour globals at the chosen theme. mode "none" uses the see-through key as BG."""
    pal = PALETTES.get(theme) or PALETTES["dark"]
    globals().update(pal)
    if mode == "none":
        globals()["BG"] = TRANSPARENT_KEY.get(theme, TRANSPARENT_KEY["dark"])

FAMILY_COLORS = {
    "fable": "#D4537E",
    "mythos": "#7F77DD",
    "opus": TEAL,
    "sonnet": BLUE,
    "haiku": AMBER,
}
OTHER_COLOR = "#888780"
SOURCE_COLORS = {
    "cowork": "#7F77DD",
    "claude_duet": "#D4537E",
    "claude_code": TEAL,
    "chat": BLUE,
}


# ----------------------------------------------------------------------------
# Pure logic (no GUI), so it can be tested anywhere
# ----------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def parse_ts(value):
    """Parse an ISO timestamp (with Z or offset) into an aware datetime."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def fmt_dur(minutes):
    """Minutes -> '2h 37m' / '4d 11h' / '12m'."""
    m = int(round(max(0, minutes)))
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {mm}m"
    return f"{mm}m"


def fmt_tokens(n):
    n = float(n)
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K"
    return str(int(n))


def _window_for(kind, key=""):
    k = f"{kind} {key}".lower()
    if "session" in k or "five_hour" in k or "5h" in k:
        return WINDOW_MIN["session"]
    if "week" in k or "seven_day" in k or "7d" in k:
        return WINDOW_MIN["weekly"]
    return None


def parse_limits(api):
    """Turn the API reply into a list of limit dicts.

    Current shape: a `limits` list, each entry like
      {"kind": "session"|"weekly_all"|"weekly_scoped", "percent": 58,
       "resets_at": "...", "scope": {"model": {"display_name": "Fable"}}}
    Older shape: flat keys five_hour / seven_day / seven_day_<model> with
    {"utilization": N, "resets_at": "..."}. Both are handled, and any model the
    account reports shows up automatically.
    """
    out = []
    limits = api.get("limits") if isinstance(api, dict) else None
    if isinstance(limits, list):
        for e in limits:
            if not isinstance(e, dict) or e.get("percent") is None:
                continue
            kind = e.get("kind") or "unknown"
            if kind == "session":
                label, order = "Current session", 0
            elif kind == "weekly_all":
                label, order = "Weekly, all models", 1
            elif kind == "weekly_scoped":
                model = (e.get("scope") or {}).get("model") or {}
                name = str(model.get("display_name") or model.get("id") or "scoped").strip()
                label, order = f"Weekly, {name}", 2
            else:
                label, order = kind.replace("_", " ").capitalize(), 3
            out.append({
                "key": f"{kind}:{label}", "label": label, "order": order,
                "pct": float(e["percent"]),
                "resets_at": parse_ts(e.get("resets_at")),
                "window_min": _window_for(kind),
            })
    elif isinstance(api, dict):
        for key, val in api.items():
            if not isinstance(val, dict) or val.get("utilization") is None:
                continue
            if key == "five_hour":
                label, order = "Current session", 0
            elif key == "seven_day":
                label, order = "Weekly, all models", 1
            elif key.startswith("seven_day_"):
                label, order = "Weekly, " + key[10:].replace("_", " ").title(), 2
            else:
                continue
            out.append({
                "key": key, "label": label, "order": order,
                "pct": float(val["utilization"]),
                "resets_at": parse_ts(val.get("resets_at")),
                "window_min": _window_for("", key),
            })
    # session, all-models, then per-model by usage (highest first)
    out.sort(key=lambda d: (d["order"], -d["pct"] if d["order"] >= 2 else 0))
    return out


def parse_breakdown(api):
    """Weekly usage by source (Cowork, Claude Code, Chats, ...) from the usage reply.

    Each row's `percent_of_limit` is that source's share of the weekly limit, so the
    rows add up to the weekly all-models percentage. Returns [] if the reply has none.
    """
    bd = api.get("seven_day_breakdown") if isinstance(api, dict) else None
    rows = bd.get("rows") if isinstance(bd, dict) else None
    out = []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict) or r.get("percent_of_limit") is None:
                continue
            key = str(r.get("key") or "")
            out.append({"key": key,
                        "name": str(r.get("display_name") or key or "Other"),
                        "pct": float(r["percent_of_limit"])})
    out.sort(key=lambda d: -d["pct"])
    return out


def project(item, now):
    """Work out whether this limit will run out before it resets.

    Returns dict: state in ok|warn|over|full|early|unknown, projected (% at reset,
    may exceed 100), remaining_min, hit_in_min, early_by_min.
    """
    pct = item["pct"]
    resets = item["resets_at"]
    win = item["window_min"]
    res = {"state": "unknown", "projected": None, "remaining_min": None,
           "hit_in_min": None, "early_by_min": None}
    if resets is not None:
        res["remaining_min"] = max(0.0, (resets - now).total_seconds() / 60)
    if pct >= 100:
        res["state"] = "full"
        return res
    if resets is None or not win:
        return res
    remaining = res["remaining_min"]
    elapsed = win - remaining
    if pct <= 0:
        res.update(state="ok", projected=0.0)
        return res
    if elapsed < max(5.0, win * 0.02):
        res["state"] = "early"
        return res
    rate = pct / elapsed  # percent per minute
    projected = pct + rate * remaining
    res["projected"] = projected
    if projected >= 100:
        hit_in = (100 - pct) / rate
        res.update(state="over", hit_in_min=hit_in,
                   early_by_min=max(0.0, remaining - hit_in))
    elif projected >= 90:
        res["state"] = "warn"
    else:
        res["state"] = "ok"
    return res


def verdict_text(p):
    s = p["state"]
    if s == "full":
        return "Limit reached", "over"
    if s == "over":
        return f"Projected to run out about {fmt_dur(p['early_by_min'])} early", "over"
    if s == "warn":
        return f"Close: on pace for {p['projected']:.0f}% at reset", "warn"
    if s == "ok":
        return "On track, you won't hit the limit", "ok"
    if s == "early":
        return "Too early in this window to project", "none"
    return "", "none"


def fmt_reset_clock(resets_at, clock24=False, now=None):
    """Local reset time: just the time if it is today, else 'Sat 12:47 AM'."""
    local = resets_at.astimezone()
    today = (now or datetime.now(timezone.utc)).astimezone().date()
    t = local.strftime("%H:%M") if clock24 else local.strftime("%I:%M %p").lstrip("0")
    return t if local.date() == today else f"{local.strftime('%a')} {t}"


def sub_text(p, resets_at=None, clock24=False):
    parts = []
    if p["remaining_min"] is not None:
        txt = f"Resets in {fmt_dur(p['remaining_min'])}"
        if resets_at is not None:
            txt += f" ({fmt_reset_clock(resets_at, clock24)})"
        parts.append(txt)
    if p["state"] == "over":
        parts.append(f"pace hits 100% in about {fmt_dur(p['hit_in_min'])}")
    elif p["state"] in ("ok", "warn") and p["projected"] is not None:
        parts.append(f"on pace for {p['projected']:.0f}% at reset")
    return " · ".join(parts)


# ---- credentials / API ----

class UsageError(Exception):
    def __init__(self, msg, kind="error"):
        super().__init__(msg)
        self.kind = kind


def claude_config_dirs():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return [Path(p.strip()) for p in env.split(",") if p.strip()]
    home = Path.home()
    return [home / ".claude", home / ".config" / "claude"]


def _creds_path():
    for d in claude_config_dirs():
        p = d / ".credentials.json"
        if p.exists():
            return p
    return claude_config_dirs()[0] / ".credentials.json"


def read_credentials():
    try:
        return json.loads(_creds_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_credentials(creds):
    """Refresh the OAuth token and write the rotated tokens back (opt-in)."""
    oauth = dict(creds.get("claudeAiOauth", {}))
    rt = oauth.get("refreshToken")
    if not rt:
        raise UsageError("Sign-in expired. Open Claude Code to sign in again.", "auth")
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": rt,
                       "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        latest = read_credentials()
        if latest and latest.get("claudeAiOauth", {}).get("refreshToken") != rt:
            return latest  # Claude Code refreshed it first
        raise UsageError(f"Token refresh failed ({e.code}). Open Claude Code to refresh it.", "auth")
    oauth["accessToken"] = result["access_token"]
    if result.get("refresh_token"):
        oauth["refreshToken"] = result["refresh_token"]
    oauth["expiresAt"] = int(time.time() * 1000 + int(result.get("expires_in", 3600)) * 1000)
    latest = read_credentials() or creds
    if latest.get("claudeAiOauth", {}).get("refreshToken") != rt:
        return latest
    updated = dict(creds)
    updated["claudeAiOauth"] = oauth
    path = _creds_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(updated), encoding="utf-8")
    os.replace(tmp, path)
    return updated


def fetch_usage(cfg):
    creds = read_credentials()
    if not creds:
        raise UsageError("No Claude Code sign-in found. Run Claude Code once and sign in.", "auth")
    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    if not token:
        raise UsageError("No access token in Claude Code's credentials.", "auth")
    if time.time() * 1000 > oauth.get("expiresAt", 0) - 60_000:
        if cfg.get("auto_refresh_token"):
            creds = refresh_credentials(creds)
            token = creds["claudeAiOauth"]["accessToken"]
        else:
            raise UsageError(
                "Sign-in expired. Open Claude Code once to refresh it, or set "
                "auto_refresh_token to true in config.json.", "auth")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": cfg.get("user_agent", "claude-code/2.1.71"),
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise UsageError("Sign-in rejected (401). Open Claude Code to refresh it.", "auth")
        if e.code == 429:
            raise UsageError("Rate limited by the usage endpoint. Retrying shortly.", "rate")
        raise UsageError(f"Usage endpoint returned HTTP {e.code}.", "http")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UsageError(f"Can't reach Anthropic ({getattr(e, 'reason', e)}). Check your connection.", "net")
    except ValueError:
        raise UsageError("Unexpected reply from the usage endpoint.", "http")


# ---- Claude Code history ----

def family_of(model):
    m = (model or "").lower()
    for fam in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return "other"


def history_dirs():
    return [d / "projects" for d in claude_config_dirs() if (d / "projects").is_dir()]


def _parse_file(path):
    recs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage")
                model = msg.get("model")
                if not u or not model or model == "<synthetic>":
                    continue
                ts = parse_ts(o.get("timestamp"))
                if ts is None:
                    continue
                recs.append((
                    ts.timestamp(), family_of(model),
                    int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                    int(u.get("cache_creation_input_tokens") or 0),
                    int(u.get("cache_read_input_tokens") or 0),
                    (msg.get("id"), o.get("requestId")),
                ))
    except OSError:
        pass
    return recs


def scan_history(dirs, cache, days=7, now=None):
    """Aggregate Claude Code tokens per local day and model family.

    "tokens" = input + output + cache writes. Cache reads are tracked separately
    (they're huge and cheap), used only for the optional cost estimate.
    """
    now = now or datetime.now()
    today = now.date()
    day_list = [(today.fromordinal(today.toordinal() - i)) for i in range(days - 1, -1, -1)]
    cutoff = datetime.combine(day_list[0], datetime.min.time()).timestamp()
    seen = set()
    by_day = {d.isoformat(): {} for d in day_list}
    for base in dirs:
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".jsonl"):
                    continue
                p = os.path.join(root, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                sig = (st.st_mtime, st.st_size)
                ent = cache.get(p)
                if not ent or ent[0] != sig:
                    ent = (sig, _parse_file(p))
                    cache[p] = ent
                for ts, fam, i, o, cw, cr, key in ent[1]:
                    if ts < cutoff:
                        continue
                    if key[0] or key[1]:
                        if key in seen:
                            continue
                        seen.add(key)
                    d = datetime.fromtimestamp(ts).date().isoformat()
                    slot = by_day.get(d)
                    if slot is None:
                        continue
                    t = slot.setdefault(fam, [0, 0, 0, 0])
                    t[0] += i
                    t[1] += o
                    t[2] += cw
                    t[3] += cr
    return {"days": [d.isoformat() for d in day_list], "by_day": by_day}


def history_tokens(slot_fam):
    return slot_fam[0] + slot_fam[1] + slot_fam[2]


def history_cost(slot, prices):
    total = 0.0
    for fam, t in slot.items():
        p = prices.get(fam)
        if not p:
            continue
        total += (t[0] * p.get("in", 0) + t[1] * p.get("out", 0)
                  + t[2] * p.get("cache_write", 0) + t[3] * p.get("cache_read", 0)) / 1e6
    return total


def demo_usage(now):
    from datetime import timedelta
    return {"limits": [
        {"kind": "session", "percent": 42, "resets_at": (now + timedelta(minutes=157)).isoformat()},
        {"kind": "weekly_all", "percent": 68, "resets_at": (now + timedelta(hours=107)).isoformat()},
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


def demo_history():
    from datetime import date, timedelta
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    base = [(3.2, 1.1, 0.2), (5.0, 2.0, 0.4), (2.1, 0.9, 0.1), (6.4, 3.0, 0.8), (4.1, 1.5, 0.3),
            (5.5, 2.2, 0.5), (3.0, 1.9, 0.9)]
    by_day = {}
    for d, (s, o, f) in zip(days, base):
        by_day[d] = {"sonnet": [int(s * 1e6), 0, 0, 0], "opus": [int(o * 1e6), 0, 0, 0],
                     "fable": [int(f * 1e6), 0, 0, 0]}
    return {"days": days, "by_day": by_day}


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

def open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: opens File Explorer at that folder
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


def run_gui(demo=False):
    import tkinter as tk
    from tkinter import font as tkfont

    cfg = load_config()
    if cfg.get("theme") not in PALETTES:
        cfg["theme"] = "dark"
    if cfg.get("window_mode") not in WINDOW_MODES:
        cfg["window_mode"] = "solid"
    apply_palette(cfg["theme"], cfg["window_mode"])
    q = queue.Queue()
    wake = threading.Event()
    state = {"usage": None, "history": None, "last_ok": None, "last_err": None,
             "started": time.time(), "force_at": 0.0}
    cache = {}

    # ---- worker thread ----
    def worker():
        while True:
            try:
                if demo:
                    now = datetime.now(timezone.utc)
                    q.put(("usage", demo_usage(now)))
                    q.put(("history", demo_history()))
                else:
                    try:
                        q.put(("usage", fetch_usage(cfg)))
                    except UsageError as e:
                        q.put(("error", e))
                    except Exception as e:  # keep the loop alive
                        q.put(("error", UsageError(f"Unexpected error: {e}")))
                    try:
                        q.put(("history", scan_history(history_dirs(), cache)))
                    except Exception:
                        pass
            finally:
                wake.wait(timeout=max(15, int(cfg.get("refresh_seconds", 60))))
                wake.clear()

    threading.Thread(target=worker, daemon=True).start()

    # ---- window ----
    root = tk.Tk()
    root.title("Claude usage")
    root.configure(bg=BG)
    root.minsize(400, 300)
    f_base = tkfont.Font(family="Segoe UI", size=10)
    f_small = tkfont.Font(family="Segoe UI", size=9)
    f_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    f_title = tkfont.Font(family="Segoe UI", size=12, weight="bold")

    root.attributes("-topmost", bool(cfg.get("always_on_top", True)))
    if cfg.get("window_x") is not None and cfg.get("window_y") is not None:
        root.geometry(f"+{int(cfg['window_x'])}+{int(cfg['window_y'])}")
    root.update()

    def set_titlebar(dark):
        try:  # dark or light title bar on Windows 10/11
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1 if dark else 0)), 4)
        except Exception:
            pass

    outer = tk.Frame(root, bg=BG, padx=16, pady=14)
    outer.pack(fill="both", expand=True)

    # header
    head = tk.Frame(outer, bg=BG)
    head.pack(fill="x")
    lbl_title = tk.Label(head, text="Claude usage", font=f_title, fg=TEXT, bg=BG)
    lbl_title.pack(side="left")

    def mkbtn(parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd, font=f_small, fg=TEXT, bg=BG,
                      activebackground=TRACK, activeforeground=TEXT, relief="flat",
                      highlightthickness=1, highlightbackground=BTN_BORDER, bd=0,
                      padx=8, pady=3, cursor="hand2")
        return b

    static_btns = []
    btn_refresh = mkbtn(head, "Refresh", lambda: do_refresh())
    btn_refresh.pack(side="right", padx=(6, 0))
    btn_pin = mkbtn(head, "", lambda: toggle_pin())
    btn_pin.pack(side="right", padx=(6, 0))
    btn_folder = mkbtn(head, "Open folder", lambda: do_open())
    btn_folder.pack(side="right")
    # Close button: only shown in "No background" mode, where there is no title bar.
    btn_close = mkbtn(head, "✕", lambda: on_close())

    status = tk.Frame(outer, bg=BG)
    status.pack(fill="x", pady=(4, 10))
    btn_mode = mkbtn(status, "", lambda: cycle_mode())
    btn_mode.pack(side="right", padx=(6, 0))
    btn_theme = mkbtn(status, "", lambda: toggle_theme())
    btn_theme.pack(side="right")
    btn_clock = mkbtn(status, "", lambda: toggle_clock())
    btn_clock.pack(side="right", padx=(0, 6))
    static_btns.extend([btn_refresh, btn_pin, btn_folder, btn_close, btn_mode, btn_theme, btn_clock])
    dot = tk.Canvas(status, width=10, height=10, bg=BG, highlightthickness=0)
    dot.pack(side="left")
    dot_id = dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
    lbl_upd = tk.Label(status, text="Starting…", font=f_small, fg=MUTED, bg=BG)
    lbl_upd.pack(side="left", padx=(6, 0))

    banner = tk.Label(outer, text="", font=f_small, fg=BANNER_FG, bg=BANNER_BG, justify="left",
                      anchor="w", wraplength=360, padx=10, pady=8)

    body = tk.Frame(outer, bg=BG)
    body.pack(fill="both", expand=True)

    class Bar(tk.Canvas):
        def __init__(self, parent):
            super().__init__(parent, height=10, bg=BG, highlightthickness=0)
            self.vals = (0, 0, BLUE, BLUE_D)
            self.bind("<Configure>", lambda e: self.redraw())

        def set(self, pct, proj, color, ghost):
            self.vals = (pct, proj, color, ghost)
            self.redraw()

        def redraw(self):
            self.delete("all")
            w = max(self.winfo_width(), 10)
            pct, proj, color, ghost = self.vals
            self.create_rectangle(0, 0, w, 10, fill=TRACK, outline="")
            if proj:
                self.create_rectangle(0, 0, w * min(proj, 100) / 100, 10, fill=ghost, outline="")
            self.create_rectangle(0, 0, w * min(pct, 100) / 100, 10, fill=color, outline="")

    def clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def chip(parent, text, kind):
        fg, bg = CHIP.get(kind, (MUTED, TRACK))
        return tk.Label(parent, text=text, font=f_small, fg=fg, bg=bg, padx=8, pady=2)

    def render_limits():
        clear(body)
        usage = state["usage"]
        if usage is None:
            tk.Label(body, text="Waiting for the first update…", font=f_base, fg=MUTED, bg=BG).pack(anchor="w")
            return
        now = datetime.now(timezone.utc)
        items = parse_limits(usage)
        if not items:
            tk.Label(body, text="The usage reply had no limits. Run with --dump to inspect it.",
                     font=f_base, fg=MUTED, bg=BG, wraplength=360, justify="left").pack(anchor="w")
        top = [i for i in items if i["order"] < 2]
        scoped = [i for i in items if i["order"] >= 2]

        def block(item, small=False):
            p = project(item, now)
            pct = item["pct"]
            col = BLUE
            ghost = BLUE_D
            if p["state"] in ("over", "full"):
                col, ghost = RED, RED_D
            elif p["state"] == "warn":
                col, ghost = AMBER, AMBER_D
            elif item["order"] >= 2 and "opus" in item["label"].lower():
                col = TEAL
            fr = tk.Frame(body, bg=BG)
            fr.pack(fill="x", pady=(0, 10 if small else 14))
            row = tk.Frame(fr, bg=BG)
            row.pack(fill="x")
            tk.Label(row, text=item["label"], font=f_base, fg=TEXT2, bg=BG).pack(side="left")
            tk.Label(row, text=f"{pct:.0f}%", font=f_bold, fg=TEXT, bg=BG).pack(side="right")
            bar = Bar(fr)
            bar.pack(fill="x", pady=(5, 0))
            proj = p["projected"] if p["state"] in ("ok", "warn", "over") else None
            bar.set(pct, proj if proj and proj > pct else 0, col, ghost)
            st = sub_text(p, item["resets_at"], bool(cfg.get("clock_24h", False)))
            if st:
                tk.Label(fr, text=st, font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(4, 0))
            vt, vk = verdict_text(p)
            if vt and (not small or vk in ("over", "warn")):
                chip(fr, vt, vk).pack(anchor="w", pady=(5, 0))

        for it in top:
            block(it)
        if scoped:
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(2, 10))
            for it in scoped:
                block(it, small=True)

        all_rows = parse_breakdown(usage)
        if all_rows:
            zeros = [r for r in all_rows if round(r["pct"]) == 0]
            show_zero = bool(cfg.get("show_zero_sources", False))
            rows = all_rows if show_zero else [r for r in all_rows if round(r["pct"]) > 0]
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(2, 10))
            hdr = tk.Frame(body, bg=BG)
            hdr.pack(fill="x", pady=(0, 6))
            tk.Label(hdr, text="Usage by source, % of weekly limit",
                     font=f_small, fg=MUTED, bg=BG).pack(side="left")
            if zeros:
                mkbtn(hdr, "Hide 0%" if show_zero else f"Show 0% ({len(zeros)})",
                      lambda: toggle_zero()).pack(side="right")
            if not rows:
                tk.Label(body, text="No usage by any source yet this week.", font=f_small,
                         fg=MUTED, bg=BG).pack(anchor="w", pady=(0, 10))
            grid = tk.Frame(body, bg=BG)
            grid.pack(fill="x", pady=(0, 10))
            grid.columnconfigure(1, weight=1)
            for i, r in enumerate(rows):
                tk.Label(grid, text=r["name"], font=f_small, fg=TEXT2, bg=BG, anchor="w",
                         width=13).grid(row=i, column=0, sticky="w", pady=3)
                bar = Bar(grid)
                bar.grid(row=i, column=1, sticky="ew", padx=8)
                bar.set(r["pct"], 0, SOURCE_COLORS.get(r["key"], OTHER_COLOR), TRACK)
                tk.Label(grid, text=f"{r['pct']:.0f}%", font=f_bold, fg=TEXT, bg=BG,
                         anchor="e").grid(row=i, column=2, sticky="e")

    def render_history():
        hist = state["history"]
        if hist is None:
            return
        for w in hist_frame.winfo_children():
            w.destroy()
        days, by_day = hist["days"], hist["by_day"]
        fams = []
        for d in days:
            for f in by_day[d]:
                if f not in fams:
                    fams.append(f)
        order = [f for f in ("fable", "mythos", "opus", "sonnet", "haiku", "other") if f in fams]
        totals = [sum(history_tokens(t) for t in by_day[d].values()) for d in days]
        week_total = sum(totals)
        row = tk.Frame(hist_frame, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="Claude Code, last 7 days", font=f_base, fg=TEXT2, bg=BG).pack(side="left")
        tk.Label(row, text=f"{fmt_tokens(week_total)} tokens", font=f_bold, fg=TEXT, bg=BG).pack(side="right")
        cv = tk.Canvas(hist_frame, height=64, bg=BG, highlightthickness=0)
        cv.pack(fill="x", pady=(6, 0))

        def draw(_e=None):
            cv.delete("all")
            w = max(cv.winfo_width(), 100)
            mx = max(totals) or 1
            slot = w / len(days)
            bw = slot * 0.62
            for i, d in enumerate(days):
                x0 = i * slot + (slot - bw) / 2
                y = 60
                for fam in order:
                    t = by_day[d].get(fam)
                    if not t:
                        continue
                    h = 56 * history_tokens(t) / mx
                    if h < 1:
                        continue
                    cv.create_rectangle(x0, y - h, x0 + bw, y,
                                        fill=FAMILY_COLORS.get(fam, OTHER_COLOR), outline="")
                    y -= h
        cv.bind("<Configure>", draw)
        draw()
        days_lbl = tk.Frame(hist_frame, bg=BG)
        days_lbl.pack(fill="x")
        for d in days:
            tk.Label(days_lbl, text=datetime.fromisoformat(d).strftime("%a")[:2], font=f_small,
                     fg=MUTED, bg=BG).pack(side="left", expand=True)
        today_slot = by_day[days[-1]]
        today_tok = sum(history_tokens(t) for t in today_slot.values())
        txt = f"Today: {fmt_tokens(today_tok)} tokens"
        prices = cfg.get("prices_per_mtok") or {}
        if prices:
            txt += f" · ${history_cost(today_slot, prices):.2f} est."
        tk.Label(hist_frame, text=txt, font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(4, 0))
        legend = tk.Frame(hist_frame, bg=BG)
        legend.pack(fill="x", pady=(6, 0))
        for fam in order:
            tot = sum(history_tokens(by_day[d][fam]) for d in days if fam in by_day[d])
            c = tk.Canvas(legend, width=10, height=10, bg=BG, highlightthickness=0)
            c.create_rectangle(0, 0, 10, 10, fill=FAMILY_COLORS.get(fam, OTHER_COLOR), outline="")
            c.pack(side="left", padx=(0, 4))
            tk.Label(legend, text=f"{fam.title()} {fmt_tokens(tot)}", font=f_small, fg=TEXT2,
                     bg=BG).pack(side="left", padx=(0, 12))
        if not order:
            tk.Label(hist_frame, text="No Claude Code activity found in the last 7 days.",
                     font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(6, 0))

    sep = tk.Frame(outer, bg=BORDER, height=1)
    sep.pack(fill="x", pady=(4, 12))
    hist_frame = tk.Frame(outer, bg=BG)
    hist_frame.pack(fill="x")

    # ---- actions ----
    def update_pin_label():
        btn_pin.config(text="On top: on" if cfg.get("always_on_top", True) else "On top: off")

    def toggle_pin():
        cfg["always_on_top"] = not cfg.get("always_on_top", True)
        root.attributes("-topmost", bool(cfg["always_on_top"]))
        update_pin_label()
        save_config(cfg)

    def restyle():
        """Recolour the fixed widgets, then rebuild the ones that are redrawn from data."""
        root.configure(bg=BG)
        for w in (outer, head, status, body, hist_frame):
            w.configure(bg=BG)
        lbl_title.configure(fg=TEXT, bg=BG)
        dot.configure(bg=BG)
        lbl_upd.configure(bg=BG, fg=MUTED)
        banner.configure(fg=BANNER_FG, bg=BANNER_BG)
        sep.configure(bg=BORDER)
        for b in static_btns:
            b.configure(fg=TEXT, bg=BG, activebackground=TRACK, activeforeground=TEXT,
                        highlightbackground=BTN_BORDER)
        btn_theme.config(text="Theme: " + cfg["theme"].title())
        btn_mode.config(text="Window: " + WINDOW_MODE_LABEL[cfg["window_mode"]])
        btn_clock.config(text="Clock: " + ("24 hr" if cfg.get("clock_24h") else "12 hr"))
        render_limits()
        render_history()

    def apply_window_mode():
        """Apply theme + window mode (solid / see-through / no background)."""
        theme, mode = cfg["theme"], cfg["window_mode"]
        apply_palette(theme, mode)
        frameless = mode == "none"
        if bool(root.overrideredirect()) != frameless:
            root.update_idletasks()
            rx, ry = root.winfo_rootx(), root.winfo_rooty()
            if frameless:  # remember the title-bar offset so the window doesn't jump
                state["frame_off"] = (rx - root.winfo_x(), ry - root.winfo_y())
                nx, ny = rx, ry
            else:
                ox, oy = state.get("frame_off", (0, 0))
                nx, ny = rx - ox, ry - oy
            root.withdraw()
            root.overrideredirect(frameless)
            root.geometry(f"+{nx}+{ny}")
            root.deiconify()
            root.attributes("-topmost", bool(cfg.get("always_on_top", True)))
        try:  # Windows: make the background colour see-through
            root.attributes("-transparentcolor", BG if frameless else "")
        except tk.TclError:
            pass
        try:
            alpha = float(cfg.get("glass_alpha", 0.82)) if mode == "glass" else 1.0
            root.attributes("-alpha", min(1.0, max(0.3, alpha)))
        except (tk.TclError, ValueError):
            pass
        if frameless:
            btn_close.pack(side="right", padx=(6, 0), before=btn_refresh)
        else:
            btn_close.pack_forget()
        root.update_idletasks()
        set_titlebar(theme == "dark")
        restyle()

    def toggle_theme():
        cfg["theme"] = "light" if cfg.get("theme") == "dark" else "dark"
        save_config(cfg)
        apply_window_mode()

    def toggle_clock():
        cfg["clock_24h"] = not cfg.get("clock_24h", False)
        btn_clock.config(text="Clock: " + ("24 hr" if cfg["clock_24h"] else "12 hr"))
        save_config(cfg)
        render_limits()

    def cycle_mode():
        cur = cfg.get("window_mode")
        i = WINDOW_MODES.index(cur) if cur in WINDOW_MODES else 0
        cfg["window_mode"] = WINDOW_MODES[(i + 1) % len(WINDOW_MODES)]
        save_config(cfg)
        apply_window_mode()

    # Dragging: with no title bar, drag the window by any empty spot.
    drag = {}

    def drag_start(e):
        if root.overrideredirect() and not isinstance(e.widget, tk.Button):
            drag["dx"], drag["dy"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def drag_move(e):
        if "dx" in drag and root.overrideredirect():
            root.geometry(f"+{e.x_root - drag['dx']}+{e.y_root - drag['dy']}")

    root.bind_all("<ButtonPress-1>", drag_start, add="+")
    root.bind_all("<B1-Motion>", drag_move, add="+")
    root.bind_all("<ButtonRelease-1>", lambda e: drag.clear(), add="+")

    def toggle_zero():
        cfg["show_zero_sources"] = not cfg.get("show_zero_sources", False)
        save_config(cfg)
        render_limits()

    def do_refresh():
        if time.time() - state["force_at"] < 3:
            return
        state["force_at"] = time.time()
        lbl_upd.config(text="Refreshing…")
        wake.set()

    def do_open():
        path = cfg.get("open_folder") or str(Path.home())
        if not open_folder(path):
            state["last_err"] = f"Couldn't open {path}. Check the open_folder path in config.json."

    # ---- ui loop ----
    blink = {"on": False}

    def tick_status():
        now = time.time()
        stale_after = int(cfg.get("stale_after_seconds", 300))
        last_ok = state["last_ok"]
        ref = last_ok if last_ok else state["started"]
        age = now - ref
        # Show the error banner right away if the very first update failed
        stale = age > stale_after or (last_ok is None and bool(state["last_err"]))
        if last_ok is None:
            txt = "Waiting for the first update…" if not state["last_err"] else "No update yet"
        elif age < 10:
            txt = "Updated just now"
        elif age < 90:
            txt = f"Updated {int(age)}s ago"
        else:
            txt = f"Updated {int(age // 60)} min ago"
        if stale and last_ok is not None:
            txt = f"Last updated {int(age // 60)} min ago"
        if stale:
            blink["on"] = not blink["on"]
            dot.itemconfig(dot_id, fill=AMBER if blink["on"] else AMBER_D)
            lbl_upd.config(text=txt, fg=AMBER if blink["on"] else "#BA7517")
            reason = state["last_err"] or f"No update for {int(age // 60)} min."
            banner.config(text=f"{reason} Numbers may be out of date. Press Refresh to retry.")
            if not banner.winfo_ismapped():
                banner.pack(fill="x", pady=(0, 10), before=body)
        else:
            dot.itemconfig(dot_id, fill=GREEN if last_ok else TRACK)
            lbl_upd.config(text=txt, fg=MUTED)
            if state["last_err"] and last_ok:
                pass
            if banner.winfo_ismapped():
                banner.pack_forget()
        root.after(1000, tick_status)

    def poll():
        changed = False
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "usage":
                    state["usage"] = payload
                    state["last_ok"] = time.time()
                    state["last_err"] = None
                    if demo and "--stale" in sys.argv:  # preview the stale warning
                        state["last_ok"] = time.time() - 420
                        state["last_err"] = "Can't reach Anthropic (demo)."
                    changed = True
                    if "--dump" in sys.argv:
                        DUMP_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                elif kind == "history":
                    state["history"] = payload
                    render_history()
                elif kind == "error":
                    state["last_err"] = str(payload)
        except queue.Empty:
            pass
        if changed:
            render_limits()
        root.after(300, poll)

    def redraw_countdowns():
        if state["usage"] is not None:
            render_limits()
        root.after(30_000, redraw_countdowns)

    def on_close():
        x, y = root.winfo_x(), root.winfo_y()
        if root.overrideredirect():  # save the position as if the title bar were there
            ox, oy = state.get("frame_off", (0, 0))
            x, y = root.winfo_rootx() - ox, root.winfo_rooty() - oy
        cfg["window_x"], cfg["window_y"] = x, y
        save_config(cfg)
        root.destroy()

    update_pin_label()
    apply_window_mode()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(300, poll)
    root.after(1000, tick_status)
    root.after(30_000, redraw_countdowns)
    root.mainloop()


def main():
    if "--demo" in sys.argv:
        run_gui(demo=True)
        return
    run_gui(demo=False)


if __name__ == "__main__":
    main()
