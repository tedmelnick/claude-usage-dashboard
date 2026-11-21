"""Pure logic (no GUI, no network): parsing the usage reply, projections, formatting."""
from datetime import datetime, timezone

WINDOW_MIN = {"session": 300, "weekly": 10080}

# How long (seconds) the app may wait between tries after repeated failures, by kind.
# 0 means "never back off" (sign-in problems are fixed on the PC, not by waiting).
BACKOFF_CAP = {"rate": 900, "http": 300, "net": 120, "format": 600, "error": 300, "auth": 0}


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


def fmt_secs(seconds):
    """Seconds -> '45s' under a minute, else like fmt_dur."""
    s = int(round(max(0, seconds)))
    return f"{s}s" if s < 60 else fmt_dur(s / 60)


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

    Each dict has key, kind, label, order, pct, resets_at, window_min.
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
            try:
                pct = float(e["percent"])
            except (TypeError, ValueError):
                continue
            out.append({
                "key": f"{kind}:{label}", "kind": kind, "label": label, "order": order,
                "pct": pct, "resets_at": parse_ts(e.get("resets_at")),
                "window_min": _window_for(kind),
            })
    elif isinstance(api, dict):
        for key, val in api.items():
            if not isinstance(val, dict) or val.get("utilization") is None:
                continue
            if key == "five_hour":
                label, order, kind = "Current session", 0, "session"
            elif key == "seven_day":
                label, order, kind = "Weekly, all models", 1, "weekly_all"
            elif key.startswith("seven_day_"):
                label, order, kind = "Weekly, " + key[10:].replace("_", " ").title(), 2, "weekly_scoped"
            else:
                continue
            try:
                pct = float(val["utilization"])
            except (TypeError, ValueError):
                continue
            out.append({
                "key": key, "kind": kind, "label": label, "order": order,
                "pct": pct, "resets_at": parse_ts(val.get("resets_at")),
                "window_min": _window_for("", key),
            })
    # session, all-models, then per-model by usage (highest first)
    out.sort(key=lambda d: (d["order"], -d["pct"] if d["order"] >= 2 else 0))
    return out


def reply_problem(api):
    """None if the reply looks usable, else a plain-language reason it doesn't.

    The usage endpoint is unofficial, so a changed format is the most likely way
    this app breaks. Only top-level field names are reported, never values.
    """
    if not isinstance(api, dict):
        return ("The usage reply wasn't the expected kind of data. Anthropic may have "
                "changed the usage endpoint. Run Debug dump.bat and check dump.json.")
    if parse_limits(api):
        return None
    keys = ", ".join(sorted(str(k) for k in api)[:12]) or "none"
    return ("Anthropic may have changed the usage format: no limits were found in the "
            f"reply (top-level fields: {keys}). Run Debug dump.bat and check dump.json, "
            "or look for an app update.")


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


def next_delay(refresh, failures, kind="error", retry_after=None):
    """Seconds to wait before the next try.

    After a success (failures == 0) that's the normal refresh interval. After
    repeated failures the wait doubles each time up to a cap for that kind of error
    (BACKOFF_CAP), and a server's Retry-After is honoured when it asks for longer.
    Sign-in problems never back off, since waiting doesn't fix them.
    """
    base = max(15, int(refresh))
    cap = BACKOFF_CAP.get(kind, 300)
    if failures <= 0 or cap == 0:
        return base
    n = failures if kind == "rate" else failures - 1
    delay = min(max(cap, base), base * (2 ** min(max(n, 0), 6)))
    if retry_after:
        try:
            delay = max(delay, min(int(retry_after), 3600))
        except (TypeError, ValueError):
            pass
    return delay
