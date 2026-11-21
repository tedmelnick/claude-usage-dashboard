import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from usage_dashboard import history
from usage_dashboard.core import parse_limits

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 20, 20, 0)  # local time; the fixture messages are "today" and "yesterday" in UTC


def make_logs(tmp_path):
    base = tmp_path / "projects"
    folder = base / "C--Dev-claude-usage-dashboard"
    folder.mkdir(parents=True)
    shutil.copy(FIX / "session.jsonl", folder / "s1.jsonl")
    # make the file look recent no matter when the tests run
    now = datetime.now().timestamp()
    os.utime(folder / "s1.jsonl", (now, now))
    return base


def test_family_and_project_name():
    assert history.family_of("claude-opus-5-5") == "opus"
    assert history.family_of("claude-fable-5-2") == "fable"
    assert history.family_of("gpt") == "other"
    assert history.project_name("C:\\Dev\\claude-usage-dashboard", "x") == "claude-usage-dashboard"
    assert history.project_name("/home/ted/webapp/", "x") == "webapp"
    assert history.project_name(None, "fallback-dir") == "fallback-dir"


def test_parse_file_skips_junk_and_synthetic(tmp_path):
    recs = history._parse_file(str(make_logs(tmp_path) / "C--Dev-claude-usage-dashboard" / "s1.jsonl"), "fb")
    # m1 appears twice (kept here, de-duplicated when summed), m2 and m4 once; synthetic and junk dropped
    assert [r[1] for r in recs] == ["opus", "opus", "sonnet", "haiku"]
    assert recs[2][7] == "webapp"
    assert recs[3][7] == "fb"  # no cwd -> folder name


def test_scan_history_totals_and_dedupe(tmp_path):
    base = make_logs(tmp_path)
    # a wide window so the fixture's fixed 2026 dates are inside it whenever this runs
    days = (datetime.now().date() - datetime(2026, 11, 19).date()).days + 1
    h = history.scan_history([base], {}, days=days)
    assert len(h["days"]) == days
    opus = h["by_project"]["claude-usage-dashboard"]["opus"]
    assert opus == [100, 50, 1000, 5000]              # counted once despite the duplicate line
    assert h["by_project"]["webapp"]["sonnet"] == [200, 100, 0, 0]
    assert history.history_tokens(opus) == 1150       # cache reads excluded
    total = sum(history.slot_tokens(h["by_day"][d]) for d in h["days"])
    assert total == 1150 + 300 + 20


def test_scan_history_cache_reuses_unchanged_files(tmp_path):
    base = make_logs(tmp_path)
    cache = {}
    history.scan_history([base], cache, days=400)
    assert len(cache) == 1
    first = next(iter(cache.values()))
    history.scan_history([base], cache, days=400)
    assert next(iter(cache.values())) is first


def test_cost_and_top_projects():
    prices = {"opus": {"in": 4, "out": 20, "cache_write": 5, "cache_read": 0.2}}
    slot = {"opus": [1_000_000, 100_000, 200_000, 5_000_000], "sonnet": [10, 10, 0, 0]}
    assert round(history.history_cost(slot, prices), 4) == round(4 + 2 + 1 + 1, 4)  # sonnet unpriced
    by_project = {"a": {"opus": [10, 10, 0, 0]}, "b": {"opus": [500, 0, 0, 0]}, "c": {"opus": [0, 0, 0, 99]}}
    rows = history.top_projects(by_project, 2, prices)
    assert [r["name"] for r in rows] == ["b", "a"]    # c has only cache reads: no tokens
    assert rows[0]["cost"] is not None
    assert history.top_projects(by_project, 5)[0]["cost"] is None


class TestSnapshots:
    def _items(self, weekly, session=10, resets=None):
        api = {"limits": [
            {"kind": "session", "percent": session, "resets_at": "2026-11-20T18:00:00Z"},
            {"kind": "weekly_all", "percent": weekly, "resets_at": resets or "2026-11-25T05:00:00Z"}]}
        return parse_limits(api)

    def test_thinned_writes(self, tmp_path):
        s = history.SnapshotStore(tmp_path / "h.db")
        assert s.record(self._items(50), now_ts=1000) == 2
        assert s.record(self._items(50), now_ts=1060) == 0            # unchanged, 1 minute later
        assert s.record(self._items(51), now_ts=1120) == 1            # weekly changed (session unchanged)
        assert s.record(self._items(51), now_ts=1120 + 700) == 2      # 10+ minutes: write again
        s.close()

    def test_window_peaks(self, tmp_path):
        s = history.SnapshotStore(tmp_path / "h.db")
        week = 7 * 86400
        end = int(datetime(2026, 11, 25, 5, tzinfo=timezone.utc).timestamp())
        for n, peak in enumerate([60, 90, 75]):            # three weekly windows
            reset = datetime.fromtimestamp(end - (2 - n) * week, tz=timezone.utc).isoformat()
            for pct in (peak - 20, peak, peak - 5):        # rises, peaks, then drifts down
                s.record(self._items(pct, resets=reset), now_ts=end - (2 - n) * week - 3600 + pct * 10)
        # same window, reset time off by seconds: must not split the window
        s.record(self._items(75, resets="2026-11-25T05:00:03Z"), now_ts=end - 60)
        peaks = s.window_peaks("weekly_all", 8, now_ts=end - 3600)
        assert [p["peak"] for p in peaks] == [60, 90, 75]
        assert [p["current"] for p in peaks] == [False, False, True]
        assert len(s.window_peaks("weekly_all", 2, now_ts=end)) == 2
        s.close()

    def test_prune(self, tmp_path):
        s = history.SnapshotStore(tmp_path / "h.db", keep_days=0)
        s.record(self._items(50), now_ts=1000)
        s.prune(30, now_ts=1000 + 40 * 86400)
        assert s.window_peaks("weekly_all") == []
        s.close()
