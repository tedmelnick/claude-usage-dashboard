"""Claude Code token history (from local logs) and saved limit snapshots (SQLite)."""
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from .api import claude_config_dirs
from .core import parse_ts


def family_of(model):
    m = (model or "").lower()
    for fam in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return "other"


def history_dirs():
    return [d / "projects" for d in claude_config_dirs() if (d / "projects").is_dir()]


def project_name(cwd, fallback):
    """Last folder of the working directory a message ran in ('' -> fallback)."""
    if cwd:
        parts = [p for p in re.split(r"[\\/]+", str(cwd)) if p]
        if parts:
            return parts[-1]
    return fallback or "unknown"


def _parse_file(path, fallback_project=""):
    import json
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
                if not isinstance(o, dict) or o.get("type") != "assistant":
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
                    project_name(o.get("cwd"), fallback_project),
                ))
    except OSError:
        pass
    return recs


def scan_history(dirs, cache, days=7, now=None):
    """Aggregate Claude Code tokens per local day, model family and project.

    "tokens" = input + output + cache writes. Cache reads are tracked separately
    (they're huge and cheap), used only for the optional cost estimate.

    Returns {"days": [iso dates], "by_day": {date: {family: [in,out,cw,cr]}},
             "by_project": {name: {family: [in,out,cw,cr]}},
             "by_project_day": {date: {name: {family: [in,out,cw,cr]}}}}.
    """
    now = now or datetime.now()
    today = now.date()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    cutoff = datetime.combine(day_list[0], datetime.min.time()).timestamp()
    seen = set()
    by_day = {d.isoformat(): {} for d in day_list}
    by_project = {}
    by_project_day = {}
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
                    rel = os.path.relpath(p, base).replace("\\", "/").split("/")[0]
                    ent = (sig, _parse_file(p, rel))
                    cache[p] = ent
                for ts, fam, i, o, cw, cr, key, proj in ent[1]:
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
                    pday = by_project_day.setdefault(d, {}).setdefault(proj, {})
                    for bucket in (slot, by_project.setdefault(proj, {}), pday):
                        t = bucket.setdefault(fam, [0, 0, 0, 0])
                        t[0] += i
                        t[1] += o
                        t[2] += cw
                        t[3] += cr
    return {"days": [d.isoformat() for d in day_list], "by_day": by_day,
            "by_project": by_project, "by_project_day": by_project_day}


def history_tokens(slot_fam):
    return slot_fam[0] + slot_fam[1] + slot_fam[2]


def slot_tokens(slot):
    return sum(history_tokens(t) for t in slot.values())


def history_cost(slot, prices):
    total = 0.0
    for fam, t in slot.items():
        p = prices.get(fam)
        if not p:
            continue
        total += (t[0] * p.get("in", 0) + t[1] * p.get("out", 0)
                  + t[2] * p.get("cache_write", 0) + t[3] * p.get("cache_read", 0)) / 1e6
    return total


def top_projects(by_project, n=5, prices=None):
    """Busiest projects first: [{"name", "tokens", "cost" (or None)}]."""
    rows = []
    for name, slot in (by_project or {}).items():
        tok = slot_tokens(slot)
        if tok <= 0:
            continue
        rows.append({"name": name, "tokens": tok,
                     "cost": history_cost(slot, prices) if prices else None})
    rows.sort(key=lambda r: -r["tokens"])
    return rows[:max(0, n)]


# ---- saved snapshots of the plan limits ----

class SnapshotStore:
    """Keeps a small history of each limit's percentage in usage_history.db.

    The usage endpoint only shows the current windows, so this is what lets you
    compare this week with earlier ones. Writes are thinned: a row is saved when the
    percentage changes, or at least every 10 minutes. Use it from one thread only.
    """

    def __init__(self, path, keep_days=365):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS snapshots ("
            "ts INTEGER NOT NULL, key TEXT NOT NULL, kind TEXT, label TEXT, "
            "pct REAL NOT NULL, resets_at INTEGER)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS snap_key_ts ON snapshots (key, ts)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS snap_kind ON snapshots (kind, resets_at)")
        # Added in 2.4.0: what each source and project used, so the stats page can add it up.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS source_peaks (week INTEGER NOT NULL, key TEXT NOT NULL, "
            "name TEXT, pct REAL NOT NULL, PRIMARY KEY (week, key))")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS project_days (day TEXT NOT NULL, name TEXT NOT NULL, "
            "tokens INTEGER NOT NULL, PRIMARY KEY (day, name))")
        self.conn.commit()
        self._last = {}
        self.prune(keep_days)

    def prune(self, keep_days, now_ts=None):
        if not keep_days:
            return
        cutoff = int((now_ts or time.time()) - int(keep_days) * 86400)
        self.conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        self.conn.commit()

    def record(self, items, now_ts=None, min_gap=600):
        """Save the current reading of each limit (items come from core.parse_limits)."""
        ts = int(now_ts or time.time())
        wrote = 0
        for it in items:
            last = self._last.get(it["key"])
            if last and last[1] == it["pct"] and ts - last[0] < min_gap:
                continue
            resets = it["resets_at"]
            self.conn.execute(
                "INSERT INTO snapshots (ts, key, kind, label, pct, resets_at) VALUES (?,?,?,?,?,?)",
                (ts, it["key"], it.get("kind"), it["label"], it["pct"],
                 int(resets.timestamp()) if resets else None))
            self._last[it["key"]] = (ts, it["pct"])
            wrote += 1
        if wrote:
            self.conn.commit()
        return wrote

    def record_sources(self, rows, resets_at, now_ts=None):
        """Keep the highest share each source reached in this week's window (rows come from
        core.parse_breakdown, resets_at is the weekly reset as a datetime)."""
        if resets_at is None or not rows:
            return
        week = int(round(resets_at.timestamp() / 3600.0))
        for r in rows:
            self.conn.execute(
                "INSERT INTO source_peaks (week, key, name, pct) VALUES (?,?,?,?) "
                "ON CONFLICT(week, key) DO UPDATE SET pct = MAX(pct, excluded.pct), name = excluded.name",
                (week, r["key"], r["name"], float(r["pct"])))
        self.conn.commit()

    def record_projects(self, by_project_day):
        """Save each day's tokens per project (the latest scan of a day replaces the old one)."""
        n = 0
        for day, projects in (by_project_day or {}).items():
            for name, slot in projects.items():
                tokens = slot_tokens(slot)
                if tokens <= 0:
                    continue
                self.conn.execute(
                    "INSERT INTO project_days (day, name, tokens) VALUES (?,?,?) "
                    "ON CONFLICT(day, name) DO UPDATE SET tokens = excluded.tokens", (day, name, tokens))
                n += 1
        if n:
            self.conn.commit()

    def window_peaks(self, kind="weekly_all", count=8, now_ts=None):
        """Highest percentage reached in each of the last `count` windows, oldest first.

        Rows are grouped by the window's reset time (to the hour, so small differences in
        the reported time don't split a window). Each item:
        {"resets_at": unix seconds, "peak": percent, "current": True for the open window}.
        """
        now_ts = now_ts or time.time()
        cur = self.conn.execute(
            "SELECT MAX(resets_at), MAX(pct) FROM snapshots "
            "WHERE kind = ? AND resets_at IS NOT NULL "
            "GROUP BY CAST(ROUND(resets_at / 3600.0) AS INTEGER) "
            "ORDER BY MAX(resets_at) DESC LIMIT ?", (kind, int(count)))
        rows = [{"resets_at": r[0], "peak": r[1], "current": r[0] > now_ts} for r in cur.fetchall()]
        rows.reverse()
        return rows

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ---- numbers for the stats page (read-only, safe to call from the window thread) ----

def read_stats(path, now=None):
    """Totals over the whole saved history: busiest day, streaks, top source and project.

    A day "stays under" when neither the session nor the weekly (all models) limit reached
    100% that day. Days with no saved readings are skipped, they neither count nor break a
    streak. Returns {"rows": 0} when there is nothing saved. "all-time" means since the
    history database began.
    """
    now = now or datetime.now()
    out = {"rows": 0}
    if not os.path.exists(str(path)):
        return out      # connecting would create an empty database
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error:
        return out
    try:
        out["rows"] = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        if not out["rows"]:
            return out
        peaks = {}
        for day, pk in conn.execute(
                "SELECT date(ts, 'unixepoch', 'localtime'), MAX(pct) FROM snapshots "
                "WHERE kind IN ('session', 'weekly_all') GROUP BY 1 ORDER BY 1"):
            peaks[day] = pk
        out["first_day"] = conn.execute(
            "SELECT date(MIN(ts), 'unixepoch', 'localtime') FROM snapshots").fetchone()[0]
        longest = run = 0
        for day in sorted(peaks):
            run = run + 1 if peaks[day] < 100 else 0
            longest = max(longest, run)
        days = sorted(peaks)
        cur = run if days and (now.date() - date.fromisoformat(days[-1])).days <= 1 else 0
        out["longest_streak"], out["current_streak"] = longest, cur
        # busiest day: the most percentage points added to the weekly limit in one day
        added, prev = {}, None
        for ts, pct, resets in conn.execute(
                "SELECT ts, pct, resets_at FROM snapshots WHERE kind = 'weekly_all' ORDER BY ts"):
            win = round((resets or 0) / 3600.0)
            if prev and prev[0] == win and pct > prev[1]:
                day = datetime.fromtimestamp(ts).date().isoformat()
                added[day] = added.get(day, 0.0) + (pct - prev[1])
            prev = (win, pct)
        if added:
            day = max(added, key=added.get)
            out["busiest"] = {"day": day, "points": added[day]}
        try:
            row = conn.execute("SELECT name, SUM(pct) FROM source_peaks GROUP BY key "
                               "ORDER BY SUM(pct) DESC LIMIT 1").fetchone()
            if row and row[1] > 0:
                out["top_source"] = {"name": row[0], "points": row[1]}
            row = conn.execute("SELECT name, SUM(tokens) FROM project_days GROUP BY name "
                               "ORDER BY SUM(tokens) DESC LIMIT 1").fetchone()
            if row and row[1] > 0:
                out["top_project"] = {"name": row[0], "tokens": row[1]}
        except sqlite3.Error:
            pass   # a database from before 2.4.0 has no source or project tables yet
    except sqlite3.Error:
        return {"rows": 0}
    finally:
        conn.close()
    return out
