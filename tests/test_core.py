import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usage_dashboard import core

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_limits_new_shape():
    items = core.parse_limits(load("usage_new.json"))
    assert [i["label"] for i in items] == [
        "Current session", "Weekly, all models", "Weekly, Sonnet", "Weekly, Fable"]
    assert [i["kind"] for i in items][:2] == ["session", "weekly_all"]
    assert items[0]["window_min"] == 300 and items[1]["window_min"] == 10080
    assert items[1]["resets_at"].tzinfo is not None  # the null-percent entry was skipped


def test_parse_limits_old_shape():
    items = core.parse_limits(load("usage_old.json"))
    assert [i["label"] for i in items] == ["Current session", "Weekly, all models", "Weekly, Opus"]
    assert items[1]["pct"] == 71.5 and items[1]["kind"] == "weekly_all"


def test_parse_breakdown_sorted():
    rows = core.parse_breakdown(load("usage_new.json"))
    assert [r["key"] for r in rows] == ["cowork", "chat", "claude_code"]
    assert core.parse_breakdown({}) == []


def test_reply_problem():
    assert core.reply_problem(load("usage_new.json")) is None
    assert core.reply_problem(load("usage_old.json")) is None
    msg = core.reply_problem(load("usage_changed.json"))
    assert "changed the usage format" in msg and "meters" in msg and "plan" in msg
    assert "max" not in msg  # field names only, never values
    assert "expected kind" in core.reply_problem([1, 2])


def _item(pct, remaining_min, window=10080):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    return {"pct": pct, "resets_at": now + timedelta(minutes=remaining_min), "window_min": window}, now


@pytest.mark.parametrize("pct,remaining,state", [
    (100, 100, "full"),
    (0, 5000, "ok"),
    (10, 10070, "early"),        # 10 minutes into a week: too soon to say
    (30, 5040, "ok"),            # half way, 30% -> on pace for 60%
    (47, 5040, "warn"),          # on pace for 94%
    (60, 5040, "over"),          # on pace for 120%
])
def test_project_states(pct, remaining, state):
    item, now = _item(pct, remaining)
    assert core.project(item, now)["state"] == state


def test_project_over_numbers():
    item, now = _item(60, 5040)
    p = core.project(item, now)
    assert round(p["projected"]) == 120
    assert round(p["hit_in_min"]) == 3360          # 40% more at 60% per 5040 min
    assert round(p["early_by_min"]) == 5040 - 3360


def test_project_without_reset_time_is_unknown():
    p = core.project({"pct": 50, "resets_at": None, "window_min": 300}, datetime.now(timezone.utc))
    assert p["state"] == "unknown"


def test_formatters():
    assert core.fmt_dur(157) == "2h 37m"
    assert core.fmt_dur(60 * 107) == "4d 11h"
    assert core.fmt_dur(0.2) == "0m"
    assert core.fmt_secs(45) == "45s" and core.fmt_secs(150) == "2m"
    assert core.fmt_tokens(999) == "999" and core.fmt_tokens(12_500) == "12K"
    assert core.fmt_tokens(3_400_000) == "3.4M" and core.fmt_tokens(2e9) == "2.0B"
    assert core.parse_ts("2026-11-20T15:00:00Z").utcoffset() == timedelta(0)
    assert core.parse_ts("nonsense") is None and core.parse_ts(None) is None


def test_sub_text_and_verdict():
    item, now = _item(60, 5040)
    p = core.project(item, now)
    text, kind = core.verdict_text(p)
    assert kind == "over" and "run out" in text
    assert "Resets in 3d 12h" in core.sub_text(p, item["resets_at"])


class TestNextDelay:
    def test_normal_interval(self):
        assert core.next_delay(60, 0) == 60
        assert core.next_delay(5, 0) == 15            # never faster than 15 s

    def test_backs_off_and_caps(self):
        assert core.next_delay(60, 1, "net") == 60
        assert core.next_delay(60, 2, "net") == 120
        assert core.next_delay(60, 9, "net") == 120   # network errors cap at 2 min
        assert core.next_delay(60, 9, "http") == 300

    def test_rate_limit_backs_off_at_once(self):
        assert core.next_delay(60, 1, "rate") == 120
        assert core.next_delay(60, 3, "rate") == 480
        assert core.next_delay(60, 20, "rate") == 900

    def test_retry_after_wins_when_longer(self):
        assert core.next_delay(60, 1, "rate", retry_after=600) == 600
        assert core.next_delay(60, 1, "rate", retry_after=5) == 120
        assert core.next_delay(60, 1, "rate", retry_after=999999) == 3600
        assert core.next_delay(60, 1, "rate", retry_after="soon") == 120

    def test_sign_in_problems_do_not_back_off(self):
        assert core.next_delay(60, 5, "auth") == 60

    def test_a_held_off_refresh_keeps_the_normal_pace(self):
        assert core.next_delay(60, 9, "wait") == 60


class TestStretchDelay:
    def test_grows_by_thirty_seconds_to_the_max(self):
        from usage_dashboard.core import stretch_delay
        assert [stretch_delay(60, 120, n) for n in range(5)] == [60, 90, 120, 120, 120]

    def test_same_value_turns_it_off(self):
        from usage_dashboard.core import stretch_delay
        assert stretch_delay(60, 60, 5) == 60 and stretch_delay(60, 30, 5) == 60
        assert stretch_delay(60, None, 5) == 60 and stretch_delay(60, "x", 5) == 60

    def test_keeps_the_minimum_interval(self):
        from usage_dashboard.core import stretch_delay
        assert stretch_delay(5, 200, 0) == 15
