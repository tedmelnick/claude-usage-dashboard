"""Tests for the extras (usage_dashboard/eggs.py). They check the mechanisms with made-up
values; nothing here depends on what the real triggers are."""
import json
from datetime import date, datetime, timedelta

import pytest

from usage_dashboard import eggs, history
from usage_dashboard.core import parse_limits

NOW = datetime(2026, 11, 21, 17, 0, 0)


def test_scramble_round_trips():
    obj = [["a", 1, {"b": [2, 3]}], "xé"]
    assert eggs.unscramble(eggs.scramble(obj)) == obj


def test_every_trigger_has_its_text():
    quips, texts = eggs._load("quips.json", {}), eggs._load("dates.json", {})
    for nid, *_rest in eggs.NUMBERS:
        assert eggs.number_line(quips, nid)
    for r in eggs.DATE_RULES:
        key = r.get("text") or r["id"]
        assert key in texts, key
    assert quips["hover"] and quips["empty"] and quips["developer"]
    assert len(set(eggs.ALL_IDS)) == len(eggs.ALL_IDS)
    for nid, *_rest in eggs.NUMBERS:
        assert nid in eggs.ALL_IDS
    for r in eggs.DATE_RULES:
        assert r["id"] in eggs.ALL_IDS


# ---- special numbers ----

TABLE = [["a", 7, "hit", 0], ["b", 20, "seq", 3]]


def watcher(tmp_path=None, cap=True):
    store = eggs.EggStore(tmp_path / "found.json" if tmp_path else None)
    return eggs.NumberWatcher(store, table=TABLE, daily_cap=cap), store


def test_exact_hit_fires_but_passing_does_not():
    w, _ = watcher()
    assert w.feed("session", 5, 1, NOW) is None
    assert w.feed("session", 9, 1, NOW) is None          # jumped over 7
    w2, _ = watcher()
    assert w2.feed("session", 6, 1, NOW) is None
    assert w2.feed("session", 7, 1, NOW) == "a"


def test_only_the_session_and_weekly_bars_count():
    w, _ = watcher()
    assert w.feed("weekly_scoped", 7, 1, NOW) is None
    assert w.feed("weekly_all", 7, 1, NOW) == "a"


def test_each_number_fires_once_a_week_and_once_a_day():
    w, store = watcher()
    assert w.feed("session", 7, 1, NOW) == "a"
    assert w.feed("session", 0, 2, NOW) is None
    assert w.feed("session", 7, 2, NOW) is None            # same week
    assert w.feed("session", 0, 3, NOW + timedelta(days=7)) is None
    assert w.feed("session", 7, 3, NOW + timedelta(days=7)) == "a"       # a new week
    # the daily cap: a second number the same day stays quiet
    w2, _ = watcher()
    assert w2.feed("session", 7, 1, NOW) == "a"
    for v in (11, 14, 17, 20):
        out = w2.feed("session", v, 1, NOW)
    assert out is None
    w3, _ = watcher(cap=False)
    assert w3.feed("session", 7, 1, NOW) == "a"
    for v in (11, 14, 17, 20):
        out = w3.feed("session", v, 1, NOW)
    assert out == "b"


def test_last_three_rule():
    w, _ = watcher(cap=False)
    outs = [w.feed("session", v, 1, NOW) for v in (11, 11, 14, 14, 17, 20)]
    assert outs[-1] == "b"                                   # readings repeating between polls are fine
    w, _ = watcher(cap=False)
    assert [w.feed("session", v, 1, NOW) for v in (10, 15, 17, 20)][-1] is None
    w, _ = watcher(cap=False)
    assert [w.feed("session", v, 1, NOW) for v in (14, 17, 20)][-1] is None   # only two steps seen
    w, _ = watcher(cap=False)
    assert [w.feed("session", v, 1, NOW) for v in (8, 11, 14, 17, 20)][-1] == "b"


def test_a_new_window_or_a_drop_starts_the_history_over():
    w, _ = watcher(cap=False)
    for v in (11, 14, 17):
        w.feed("session", v, 1, NOW)
    assert w.feed("session", 20, 2, NOW) is None             # the window changed in between
    w, _ = watcher(cap=False)
    for v in (11, 14, 4, 17):
        w.feed("session", v, 1, NOW)
    assert w.feed("session", 20, 1, NOW) is None


def test_store_remembers_between_runs(tmp_path):
    w, store = watcher(tmp_path)
    assert w.feed("session", 7, 1, NOW) == "a"
    again = eggs.EggStore(tmp_path / "found.json")
    assert again.data["fired"]["a"] == eggs.week_key(NOW)
    assert again.data["last_flash"] == NOW.date().isoformat()
    assert eggs.EggStore(tmp_path / "missing.json").found_count() == 0
    (tmp_path / "bad.json").write_text("{not json")
    assert eggs.EggStore(tmp_path / "bad.json").data["found"] == {}


def test_mark_counts_each_find_once():
    s = eggs.EggStore()
    assert s.mark(eggs.ALL_IDS[0]) is True
    assert s.mark(eggs.ALL_IDS[0]) is False
    assert s.mark("not-a-thing") is False
    assert s.found_count() == 1


# ---- keys, clicks, quips ----

def test_sequence_matcher_handles_restarts_and_case():
    m = eggs.SequenceMatcher(["Up", "Up", "Down", "x"])
    assert [m.feed(k) for k in ("up", "up", "up", "down", "x")] == [False, False, False, False, True]   # a false start first
    assert [m.feed(k) for k in ("UP", "UP", "DOWN", "X")][-1] is True
    assert m.feed("up") is False and m.feed("down") is False


def test_click_counter_needs_quick_clicks():
    t = [0.0]
    c = eggs.ClickCounter(3, gap=1.0, clock=lambda: t[0])
    results = []
    for dt in (0, 0.5, 0.5, 2.0, 0.5, 0.5):
        t[0] += dt
        results.append(c.hit())
    assert results == [False, False, True, False, False, True]


def test_quips_never_repeat_and_custom_ones_join(tmp_path):
    q = {"hover": ["a", "b", "c"]}
    last = None
    for _ in range(50):
        got = eggs.pick_quip(q, last)
        assert got != last
        last = got
    assert eggs.pick_quip({"hover": ["only"]}, "only") == "only"
    (tmp_path / "quips_custom.json").write_text(json.dumps({"hover": ["mine"]}))
    assert "mine" in eggs.load_quips(tmp_path)["hover"]
    assert "mine" not in eggs.load_quips()["hover"]


# ---- holidays ----

def test_easter_dates():
    assert eggs.easter(2026) == date(2026, 4, 5)
    assert eggs.easter(2027) == date(2027, 3, 28)
    assert eggs.easter(2025) == date(2025, 4, 20)


def test_holiday_rules():
    cases = {date(2026, 11, 26): "thanks", date(2026, 5, 25): "mem", date(2026, 5, 17): "ems",
             date(2026, 5, 23): "ems", date(2026, 12, 25): "xmas", date(2026, 12, 28): "kwanzaa",
             date(2027, 1, 1): "ny", date(2026, 7, 4): "jul4", date(2026, 11, 8): "diwali",
             date(2026, 2, 17): "lunar", date(2027, 3, 28): "easter"}
    for day, want in cases.items():
        h, n = eggs.holiday_on(day)
        assert h and h["id"] == want, (day, h)
    assert eggs.holiday_on(date(2026, 3, 3)) == (None, 0)
    h, n = eggs.holiday_on(date(2026, 12, 7))
    assert h["id"] == "hanuk" and n == 4
    assert eggs.badge_text(h, n) == "Hanukkah · night 4"


def test_holiday_file_is_well_formed():
    data = eggs._load("holidays.json", {})
    for h in data["holidays"]:
        for c in ([h.get("ring")] if h.get("ring") else []) + h["strip"]:
            assert len(c) == 7 and c.startswith("#") and int(c[1:], 16) >= 0, (h["id"], c)
        for y in range(2026, 2036):
            assert eggs._span(h["rule"], y, data["tables"]), (h["id"], y)
    assert len({h["id"] for h in data["holidays"]}) == len(data["holidays"])


def test_custom_holiday_data():
    data = {"tables": {"t": {"2026": "03-03"}},
            "holidays": [{"id": "x", "rule": "table:t:2"}, {"id": "y", "rule": "last:fri:mar"},
                         {"id": "w", "rule": "range:12-30:01-02"}]}
    assert eggs.holiday_on(date(2026, 3, 4), data)[0]["id"] == "x"
    assert eggs.holiday_on(date(2026, 3, 5), data) == (None, 0)
    assert eggs.holiday_on(date(2026, 3, 27), data)[0]["id"] == "y"
    assert eggs.holiday_on(date(2027, 1, 2), data)[0]["id"] == "w"
    assert eggs.holiday_on(date(2027, 3, 4), data) == (None, 0)   # no table row for 2027


# ---- dated lines ----

RULES = [{"id": "f13", "kind": "f13"},
         {"id": "night", "hours": [0, 4], "pri": 40},
         {"id": "d1", "md": [2, 3], "pri": 50},
         {"id": "d1", "md": [2, 3], "at": [10, 30], "text": "d1_at", "pri": 60},
         {"id": "cd", "md": [5, 6], "at": [23, 59], "pri": 90},
         {"id": "q", "md": [7, 8], "quiet": True, "pri": 100}]
TEXTS = {"night": "late", "d1": "plain", "d1_at": "exact", "cd": "in {s}", "q": "quiet line",
         "f13": {"session": "S", "scoped": "X {name}"}, "bday": "hb{name}", "game": "night!"}


def state(dt, **kw):
    return eggs.date_state(dt, texts=TEXTS, rules=RULES, holidays={"holidays": []}, **kw)


def test_date_rules():
    assert state(datetime(2026, 4, 1, 12))["line"] is None
    assert state(datetime(2026, 4, 1, 2))["line"] == ("night", "late")
    assert state(datetime(2026, 2, 3, 9))["line"] == ("d1", "plain")
    assert state(datetime(2026, 2, 3, 10, 30))["line"] == ("d1", "exact")
    assert state(datetime(2026, 5, 6, 23, 59, 15))["line"] == ("cd", "in 45s")
    assert state(datetime(2026, 5, 6, 23, 58))["line"] is None
    assert state(datetime(2026, 2, 3, 2))["line"] == ("d1", "plain")   # higher priority beats the night line


def test_friday_the_thirteenth():
    assert state(datetime(2026, 11, 13, 12))["labels"] is True       # a Friday
    assert state(datetime(2026, 10, 13, 12))["labels"] is False      # a Tuesday
    item = {"kind": "session", "label": "Current session"}
    assert eggs.spooky_label(item, TEXTS) == "S"
    assert eggs.spooky_label({"kind": "weekly_scoped", "label": "Weekly, Opus"}, TEXTS) == "X Opus"
    assert eggs.spooky_label({"kind": "weekly_all", "label": "Weekly, all models"}, TEXTS) == "Weekly, all models"


def test_quiet_day_and_its_switch():
    s = state(datetime(2026, 7, 8, 12))
    assert s["quiet"] and s["line"] == ("q", "quiet line") and s["holiday"] == (None, 0)
    off = state(datetime(2026, 7, 8, 12), quiet_days=False)
    assert not off["quiet"] and off["line"] is None


def test_personal_dates_come_only_from_the_custom_file():
    assert state(datetime(2026, 8, 21, 12))["line"] is None
    custom = {"birthday": "08-21", "name": "Pat",
              "league_nights": [{"day": "thu", "from": "19:00", "to": "23:00"}]}
    assert state(datetime(2026, 8, 21, 12), custom=custom)["line"] == ("bday", "hb, Pat")
    assert state(datetime(2026, 8, 21, 12), custom={"birthday": "08-21"})["line"] == ("bday", "hb")
    thursday = datetime(2026, 11, 19, 20, 0)
    assert state(thursday, custom=custom)["line"] == ("game", "night!")
    assert state(datetime(2026, 11, 19, 12), custom=custom)["line"] is None
    assert state(datetime(2026, 11, 20, 20), custom=custom)["line"] is None


def test_calm_days_keep_the_accent_but_not_the_jokes():
    s = eggs.date_state(datetime(2026, 5, 25, 12), texts=TEXTS, rules=RULES)
    assert s["calm"] and s["holiday"][0]["id"] == "mem"


# ---- stats ----

def test_stats_and_streak_text():
    q = {"streak": "Streak: {n} day{s}", "streak_none": "none"}
    assert eggs.streak_text({"current_streak": 1}, q) == "Streak: 1 day"
    assert eggs.streak_text({"current_streak": 5}, q) == "Streak: 5 days"
    assert eggs.streak_text({"current_streak": 0}, q) == "none"
    assert eggs.streak_text(None, q) == "none"
    assert eggs.stats_rows({"rows": 0}) == []
    rows = dict(eggs.stats_rows({"rows": 5, "longest_streak": 4, "current_streak": 2, "first_day": "2026-01-01",
                                 "busiest": {"day": "2026-01-02", "points": 12.0},
                                 "top_source": {"name": "Cowork", "points": 30.0},
                                 "top_project": {"name": "app", "tokens": 3_000_000}}))
    assert rows["Longest streak"].startswith("4") and "Cowork" in rows["Top source"]
    assert "app" in rows["Top project"] and rows["Since"] == "2026-01-01"


def seed(db, readings):
    """readings: (datetime, kind, pct, resets datetime)"""
    st = history.SnapshotStore(db)
    for when, kind, pct, resets in readings:
        st.conn.execute("INSERT INTO snapshots (ts, key, kind, label, pct, resets_at) VALUES (?,?,?,?,?,?)",
                        (int(when.timestamp()), kind, kind, kind, pct, int(resets.timestamp())))
    st.conn.commit()
    return st


def test_read_stats_streaks_and_busiest_day(tmp_path):
    db = tmp_path / "h.db"
    wk1, wk2 = datetime(2026, 11, 16, 9), datetime(2026, 11, 23, 9)
    base = datetime(2026, 11, 10, 12)
    rows = []
    # days 10..13 stay under; day 14 hits 100 on the session; 15 has no data; 16, 17 under
    for d, sess in ((10, 40), (11, 60), (12, 20), (13, 90), (14, 100), (16, 10), (17, 50)):
        rows.append((base.replace(day=d), "session", sess, base.replace(day=d) + timedelta(hours=3)))
    # the weekly bar climbs 10 -> 25 on the 16th and 25 -> 30 on the 17th
    rows += [(datetime(2026, 11, 16, 8), "weekly_all", 10, wk1 + timedelta(days=0)),
             (datetime(2026, 11, 16, 20), "weekly_all", 25, wk1 + timedelta(days=0)),
             (datetime(2026, 11, 17, 8), "weekly_all", 30, wk1 + timedelta(days=0))]
    st = seed(db, rows)
    st.record_sources([{"key": "cowork", "name": "Cowork", "pct": 20.0}, {"key": "chat", "name": "Chats", "pct": 5.0}], wk1)
    st.record_sources([{"key": "cowork", "name": "Cowork", "pct": 12.0}], wk1)   # lower: keeps 20
    st.record_sources([{"key": "cowork", "name": "Cowork", "pct": 10.0}], wk2)
    st.record_projects({"2026-11-16": {"app": {"opus": [100, 50, 0, 0]}, "tiny": {"opus": [1, 0, 0, 0]}},
                        "2026-11-17": {"app": {"opus": [200, 0, 0, 0]}}})
    st.record_projects({"2026-11-17": {"app": {"opus": [300, 0, 0, 0]}}})       # a rescan replaces that day
    st.close()
    out = history.read_stats(db, now=datetime(2026, 11, 18, 9))
    assert out["rows"] == 10 and out["first_day"] == "2026-11-10"
    assert out["longest_streak"] == 4          # 10-13; day 14 hit the limit; 16-17 is a run of 2
    assert out["current_streak"] == 2
    assert out["busiest"]["day"] == "2026-11-16" and out["busiest"]["points"] == pytest.approx(15)
    assert out["top_source"] == {"name": "Cowork", "points": 30.0}
    assert out["top_project"] == {"name": "app", "tokens": 450}
    # an old streak does not count as current
    assert history.read_stats(db, now=datetime(2026, 12, 25))["current_streak"] == 0


def test_read_stats_handles_missing_and_old_databases(tmp_path):
    assert history.read_stats(tmp_path / "nope.db") == {"rows": 0}
    assert not (tmp_path / "nope.db").exists()
    db = tmp_path / "old.db"
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE snapshots (ts INTEGER NOT NULL, key TEXT NOT NULL, kind TEXT, label TEXT, "
              "pct REAL NOT NULL, resets_at INTEGER)")
    c.execute("INSERT INTO snapshots VALUES (1000000000, 'k', 'session', 'k', 5, 1000003600)")
    c.commit()
    c.close()
    out = history.read_stats(db)
    assert out["rows"] == 1 and "top_source" not in out and "top_project" not in out


def test_scan_history_reports_projects_by_day(tmp_path):
    from tests.test_history import NOW, make_logs
    out = history.scan_history([make_logs(tmp_path)], {}, 7, now=NOW)
    assert "by_project_day" in out
    total = sum(history.slot_tokens(slot) for day in out["by_project_day"].values() for slot in day.values())
    assert total == sum(history.slot_tokens(s) for s in out["by_project"].values())


def test_commit_hash_reads_git_files(tmp_path):
    assert eggs.commit_hash(tmp_path) == ""
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("abcdef1234567890\n")
    assert eggs.commit_hash(tmp_path) == "abcdef1"
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack\n1234567aaaa refs/heads/main\n")
    assert eggs.commit_hash(tmp_path) == "1234567"
    (git / "HEAD").write_text("fedcba9876\n")
    assert eggs.commit_hash(tmp_path) == "fedcba9"
