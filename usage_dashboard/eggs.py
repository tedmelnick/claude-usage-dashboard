"""Small extras: seasonal accents, a few dated lines and some things to find.

Pure logic only (no tkinter), so it can be tested anywhere. The window code in gui.py
decides how to show what this module works out.

Trigger values live in the three scrambled blobs below, not in plain text. That is a
light screen, not a lock: anyone who reads the code can read them. The jokes themselves
are plain, editable files in usage_dashboard/data/. Personal bits (a birthday, league
nights, extra quips) go in files next to config.json that git ignores. Decode a blob
with `python -c "from usage_dashboard import eggs; print(eggs.NUMBERS)"`.
"""
import base64
import json
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
_KEY = b"usage-meter"


def _xor(raw):
    return bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(raw))


def scramble(obj):
    return base64.b64encode(_xor(json.dumps(obj, separators=(",", ":")).encode())).decode()


def unscramble(blob):
    return json.loads(_xor(base64.b64decode(blob)).decode())


_NUMBERS = "LihDCVQPQVZCSVAGFhBFSRkwSS9HHEdRTVNXAU8NHRFQWUM8Sz4PA1ZWSUZAX0MUAFxPSUE4Xi5RD1NHAVlcWEcaHAdDS1VwQT5WC0dXX1deSQ8FDABHXkUuTTxHQ1tHWFxCWVEJDhEPQVUpOA=="
_DATES = "LghDDgEPV0cSVEFXX0MMDEMJR05HFERAQxpJVk8MEEdIVx0IAA1ZT0lWDR0AARJFX3ZdSUA4XlcDEw5HF1lVCUkJVxoFRV8PHQxWSVAYF0NdPh5BVEA4XlcDEw5HF1tVCUkJVxoFRV8PHQxWSVAYF0NdPh5BVEA4XlcSFUVfdlxWWFBLKF9DEwBVGUdORwIcLAATRwFPFQYMUE9FVBpJVk8MEEdIVx4AHlEPQUcZAVBPKFRLUXBBRwQXG1dJVFcYARZHHQFQT1ENAgRdT0lWCBZXSTpVSR9UOFhHAgcaQ11QHRBJD0cbEVFbRQtUCEdYRx8RUVs8VB9BVkU4XlcSFUVfdl9WWFBLKF9DFxdET19NVQ9ZCEMOAQ9XRxocUFlRDANHFzZUWFQvWVERFQwPV1BEGF4OUQgDRxdPEU1UQ1dfQwoBD1c+TUlDRC5NRRRYBAAAR0gBARQCSQ8dFx1HSERDURpJVk8MEEdIVwdYVlQPQUcZAVBPKFhLVBwwSVYEBldJOl9JGVs4WEcGEAsVRV8PGVxFVC0UB0NLR1wYDBERUE8HExIAAU8VBgxQT0JRVhhw"
_KEYS = "LlE0F0cBTzAER15XNw4QCw9BRzAKBRtRTUUpSAsRVklQJxoGDxEPQUc4ABQBUU1FN0QKDQBHXlcRQ0tHTE84"

NUMBERS = unscramble(_NUMBERS)     # [id, value, "hit" | "seq", step]
DATE_RULES = unscramble(_DATES)
KEY_SEQUENCE = unscramble(_KEYS)

# Everything there is to find; the developer panel counts these.
ALL_IDS = (["konami", "dev", "hover", "empty", "streak", "stats", "season"]
           + [n[0] for n in NUMBERS]
           + ["f13", "night", "pi", "may4", "leap", "nye", "ny", "t911", "bday", "game"])

BARS = ("session", "weekly_all")   # the numbers only count on these two bars
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _load(name, default):
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_custom(folder, name):
    """Personal data next to config.json, e.g. dates_custom.json. {} if there is none."""
    try:
        data = json.loads((Path(folder) / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ---- what has been found, and what has already fired ----

class EggStore:
    """found.json: which things were found, which numbers fired this week, the last flash day.
    path=None keeps it in memory only (used by the demo previews)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {"found": {}, "fired": {}, "last_flash": ""}
        if self.path:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for k in self.data:
                        if isinstance(loaded.get(k), type(self.data[k])):
                            self.data[k] = loaded[k]
            except (OSError, ValueError):
                pass

    def save(self):
        if self.path:
            try:
                self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            except OSError:
                pass

    def mark(self, egg_id):
        """Remember a find. True the first time."""
        if egg_id in self.data["found"] or egg_id not in ALL_IDS:
            return False
        self.data["found"][egg_id] = int(time.time())
        self.save()
        return True

    def found_count(self):
        return len([i for i in self.data["found"] if i in ALL_IDS])


# ---- the special numbers ----

def week_key(now):
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


class NumberWatcher:
    """Looks for an exact reading on the session and weekly bars.

    Rules: an exact hit only (a jump past a number does nothing); each number fires once
    a week; at most one flash a day in total. "seq" numbers also need the last three
    changes in the reading to have each been +step (24, 28, 32, 36 for step 4).
    """

    def __init__(self, store, table=None, daily_cap=True):
        self.store = store
        self.daily_cap = daily_cap
        self.table = {n[0]: (n[1], n[2], n[3]) for n in (table if table is not None else NUMBERS)}
        self.hist = {}      # bar -> {"window": id, "vals": [recent distinct readings]}

    def feed(self, bar, pct, window, now):
        """One new reading. Returns the id of a number that should flash, or None."""
        if bar not in BARS:
            return None
        val = int(round(pct))
        h = self.hist.get(bar)
        if h is None or h["window"] != window or (h["vals"] and val < h["vals"][-1]):
            h = self.hist[bar] = {"window": window, "vals": []}
        if not h["vals"] or h["vals"][-1] != val:
            h["vals"] = (h["vals"] + [val])[-4:]
            changed = True
        else:
            changed = False
        if not changed:
            return None
        for nid, (target, mode, step) in self.table.items():
            if val != target:
                continue
            if mode == "seq" and h["vals"] != [val - 3 * step, val - 2 * step, val - step, val]:
                continue
            wk = week_key(now)
            if self.store.data["fired"].get(nid) == wk:
                continue
            today = now.date().isoformat()
            if self.daily_cap and self.store.data["last_flash"] == today:
                continue
            self.store.data["fired"][nid] = wk
            self.store.data["last_flash"] = today
            self.store.mark(nid)
            self.store.save()
            return nid
        return None


def number_line(quips, nid):
    return (quips.get("numbers") or {}).get(nid, "")


# ---- key sequences ----

class SequenceMatcher:
    """feed() each key; True when the whole sequence has just been typed."""

    def __init__(self, seq, fold=True):
        self.fold = fold
        self.seq = [s.lower() if fold else s for s in seq]
        self.recent = []

    def feed(self, key):
        self.recent = (self.recent + [key.lower() if self.fold else key])[-len(self.seq):]
        if self.recent == self.seq:
            self.recent = []
            return True
        return False


class ClickCounter:
    """hit() returns True on the nth click in a row (each within `gap` seconds of the last)."""

    def __init__(self, n, gap=2.0, clock=time.time):
        self.n, self.gap, self.clock = n, gap, clock
        self.count, self.last = 0, 0.0

    def hit(self):
        now = self.clock()
        self.count = self.count + 1 if now - self.last <= self.gap else 1
        self.last = now
        if self.count >= self.n:
            self.count = 0
            return True
        return False


# ---- hover quips ----

def load_quips(folder=None):
    q = _load("quips.json", {})
    if folder is not None:
        extra = load_custom(folder, "quips_custom.json")
        if isinstance(extra.get("hover"), list):
            q = dict(q, hover=list(q.get("hover", [])) + [str(s) for s in extra["hover"]])
    return q


def pick_quip(quips, last=None, rng=random):
    """A line other than the last one shown."""
    pool = [s for s in quips.get("hover", []) if s != last] or list(quips.get("hover", []))
    return rng.choice(pool) if pool else ""


# ---- holidays ----

def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _md(text, year):
    m, d = text.split("-")
    return date(year, int(m), int(d))


def _nth_weekday(year, month, wd, n):
    first = date(year, month, 1)
    return first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, wd):
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - wd) % 7)


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _span(rule, year, tables):
    """(first day, number of days) of a holiday rule in a given year, or None."""
    p = rule.split(":")
    kind = p[0]
    try:
        if kind == "fixed":
            return _md(p[1], year), 1
        if kind == "range":
            a = _md(p[1], year)
            b = _md(p[2], year)
            if b < a:
                b = _md(p[2], year + 1)
            return a, (b - a).days + 1
        if kind == "nth":
            start = _nth_weekday(year, MONTHS.index(p[3]) + 1, WEEKDAYS.index(p[1]), int(p[2]))
            return start, int(p[4]) if len(p) > 4 else 1
        if kind == "last":
            return _last_weekday(year, MONTHS.index(p[2]) + 1, WEEKDAYS.index(p[1])), 1
        if kind == "easter":
            return easter(year), 1
        if kind == "table":
            text = (tables.get(p[1]) or {}).get(str(year))
            return (_md(text, year), int(p[2])) if text else None
    except (ValueError, IndexError):
        return None
    return None


def holiday_on(day, data=None):
    """(holiday dict, day number within it starting at 1) for a date, or (None, 0)."""
    data = data if data is not None else _load("holidays.json", {})
    tables = data.get("tables") or {}
    for h in data.get("holidays") or []:
        for year in (day.year, day.year - 1):
            sp = _span(h.get("rule", ""), year, tables)
            if sp and sp[0] <= day < sp[0] + timedelta(days=sp[1]):
                return h, (day - sp[0]).days + 1
    return None, 0


# ---- dated lines ----

def _weekday_ok(spec, now):
    return WEEKDAYS[now.weekday()] == str(spec).lower()[:3]


def _minutes(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def date_state(now, custom=None, texts=None, quiet_days=True, rules=None, holidays=None):
    """What today looks like. Returns a dict:
      line   (egg id, text) or None   the one line to show under the title
      labels bool                     limit labels turn spooky
      quiet  bool                     a quiet day: everything else stays off
      calm   bool                     no jokes today (accents stay)
      holiday (dict, day number)      the seasonal accent, or (None, 0)
    """
    texts = texts if texts is not None else _load("dates.json", {})
    custom = custom or {}
    rules = rules if rules is not None else DATE_RULES
    best = (-1, None)
    out = {"line": None, "labels": False, "quiet": False, "calm": False, "holiday": (None, 0)}

    def offer(pri, egg_id, text):
        nonlocal best
        if text and pri > best[0]:
            best = (pri, (egg_id, text))

    for r in rules:
        if r.get("kind") == "f13":
            if now.weekday() == 4 and now.day == 13:
                out["labels"] = True
            continue
        if "hours" in r:
            if r["hours"][0] <= now.hour < r["hours"][1]:
                offer(r.get("pri", 0), r["id"], texts.get(r["id"], ""))
            continue
        if "md" not in r or (now.month, now.day) != tuple(r["md"]):
            continue
        if r.get("quiet"):
            if not quiet_days:
                continue
            out["quiet"] = True
        if "at" in r and (now.hour, now.minute) != tuple(r["at"]):
            continue
        key = r.get("text") or r["id"]
        text = texts.get(key, "")
        text = text.replace("{s}", f"{60 - now.second}s")   # a countdown line
        offer(r.get("pri", 0), r["id"], text)

    bday = str(custom.get("birthday") or "")
    if bday and now.strftime("%m-%d") == bday:
        who = str(custom.get("name") or "").strip()
        offer(80, "bday", (texts.get("bday") or "").replace("{name}", f", {who}" if who else ""))
    for n in custom.get("league_nights") or []:
        try:
            if _weekday_ok(n.get("day", ""), now) and \
                    _minutes(n["from"]) <= now.hour * 60 + now.minute < _minutes(n["to"]):
                offer(70, "game", texts.get("game", ""))
        except (KeyError, ValueError, AttributeError):
            continue

    if best[1] is not None:
        out["line"] = best[1]
    if out["quiet"]:
        return out      # a quiet day shows nothing else
    h, n = holiday_on(now.date(), holidays)
    out["holiday"] = (h, n)
    out["calm"] = bool(h and h.get("calm"))
    return out


def spooky_label(item, texts):
    f = texts.get("f13") or {}
    if item["kind"] in f:
        return f[item["kind"]]
    if item["kind"] == "weekly_scoped" and f.get("scoped"):
        return f["scoped"].replace("{name}", item["label"].replace("Weekly, ", ""))
    return item["label"]


def badge_text(holiday, day_no):
    return str(holiday.get("badge") or holiday.get("name") or "").replace("{n}", str(day_no))


# ---- stats and streak text ----

def streak_text(stats, quips):
    n = int((stats or {}).get("current_streak") or 0)
    if n <= 0:
        return quips.get("streak_none", "No streak yet")
    return (quips.get("streak") or "Streak: {n} day{s}").replace("{n}", str(n)).replace("{s}", "" if n == 1 else "s")


def stats_rows(stats):
    """Label/value rows for the stats page ([] when there is nothing yet)."""
    if not stats or not stats.get("rows"):
        return []
    out = []
    b = stats.get("busiest")
    out.append(("Busiest day", f"{b['day']} (+{b['points']:.0f} points of the weekly limit)" if b else "not enough yet"))
    out.append(("Longest streak", f"{stats.get('longest_streak', 0)} days under the limit"))
    out.append(("Current streak", f"{stats.get('current_streak', 0)} days"))
    s = stats.get("top_source")
    out.append(("Top source", f"{s['name']} ({s['points']:.0f} points of weekly limit, all weeks)" if s else "not enough yet"))
    p = stats.get("top_project")
    out.append(("Top project", f"{p['name']} ({p['tokens'] / 1e6:.1f}M tokens)" if p else "not enough yet"))
    out.append(("Since", stats.get("first_day") or "-"))
    return out


# ---- developer panel ----

def commit_hash(app_dir):
    """Short commit of a git clone, read straight from .git ('' if not a clone)."""
    git = Path(app_dir) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(None, 1)[1]
            try:
                return (git / ref).read_text(encoding="utf-8").strip()[:7]
            except OSError:
                for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:7]
                return ""
        return head[:7]
    except OSError:
        return ""
