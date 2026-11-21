from datetime import datetime, timedelta, timezone

from usage_dashboard import alerts
from usage_dashboard.core import parse_limits

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def items(session=None, weekly=None, remaining=5040):
    lim = []
    resets = (NOW + timedelta(minutes=remaining)).isoformat()
    if session is not None:
        lim.append({"kind": "session", "percent": session, "resets_at": (NOW + timedelta(minutes=150)).isoformat()})
    if weekly is not None:
        lim.append({"kind": "weekly_all", "percent": weekly, "resets_at": resets})
    return parse_limits({"limits": lim})


def kinds(res):
    return [(a.kind, a.key.split(":")[0]) for a in res]


def test_first_reading_is_only_a_baseline():
    t = alerts.AlertTracker()
    assert t.evaluate(items(weekly=85), NOW) == []          # already past 80: stay quiet
    assert t.evaluate(items(weekly=86), NOW) == []


def test_threshold_fires_once_and_picks_highest():
    t = alerts.AlertTracker(on_runout=False)
    t.evaluate(items(weekly=70), NOW)
    r = t.evaluate(items(weekly=82), NOW)
    assert kinds(r) == [("threshold", "weekly_all")] and "82%" in r[0].message
    assert t.evaluate(items(weekly=84), NOW) == []
    r = t.evaluate(items(weekly=95), NOW)                   # crossed 90
    assert len(r) == 1 and "95%" in r[0].message
    r = t.evaluate(items(weekly=100), NOW)
    assert kinds(r) == [("full", "weekly_all")]
    assert t.evaluate(items(weekly=100), NOW) == []


def test_jumping_over_several_levels_gives_one_alert():
    t = alerts.AlertTracker(on_runout=False)
    t.evaluate(items(weekly=10), NOW)
    assert len(t.evaluate(items(weekly=93), NOW)) == 1


def test_runout_fires_once_per_episode():
    t = alerts.AlertTracker(thresholds=[], on_reset=False)
    t.evaluate(items(weekly=20), NOW)                        # fine
    r = t.evaluate(items(weekly=60), NOW)                    # half way through the week at 60%: on pace for 120%
    assert kinds(r) == [("runout", "weekly_all")] and "before it resets" in r[0].message
    assert t.evaluate(items(weekly=62), NOW) == []
    t.evaluate(items(weekly=20), NOW)                        # pace recovers (state ok again)
    assert kinds(t.evaluate(items(weekly=60), NOW)) == [("runout", "weekly_all")]


def test_reset_alert_and_rearm():
    t = alerts.AlertTracker(on_runout=False)
    t.evaluate(items(session=88), NOW)
    r = t.evaluate(items(session=3), NOW)
    assert kinds(r) == [("reset", "session")] and "reset" in r[0].title.lower()
    assert t.evaluate(items(session=60), NOW) == []          # 80/90 re-armed but not reached
    assert kinds(t.evaluate(items(session=81), NOW)) == [("threshold", "session")]


def test_small_drop_or_low_usage_is_not_a_reset():
    t = alerts.AlertTracker(on_runout=False)
    t.evaluate(items(session=30), NOW)
    assert t.evaluate(items(session=2), NOW) == []           # never reached the 50% minimum
    t2 = alerts.AlertTracker(on_runout=False)
    t2.evaluate(items(session=70), NOW)
    assert t2.evaluate(items(session=65), NOW) == []         # only 5 points


def test_switches_off():
    t = alerts.AlertTracker(thresholds=[], on_runout=False, on_reset=False)
    t.evaluate(items(weekly=10), NOW)
    r = t.evaluate(items(weekly=100), NOW)
    assert kinds(r) == [("full", "weekly_all")]              # reaching 100 always alerts


def test_notification_command_keeps_text_out_of_the_script():
    evil = "x'; Remove-Item C:\\ -Recurse; '"
    argv, env = alerts.notification_command("Title", evil, platform="win32")
    assert argv[0] == "powershell" and evil not in " ".join(argv)
    assert env == {"CUD_TITLE": "Title", "CUD_MESSAGE": evil}
    argv, env = alerts.notification_command("T", "M", platform="darwin")
    assert argv[0] == "osascript" and argv[-2:] == ["T", "M"]
    argv, env = alerts.notification_command("T", "M", platform="linux")
    assert argv[0] == "notify-send" and argv[-2:] == ["T", "M"]


def test_notify_survives_missing_tool(monkeypatch):
    monkeypatch.setattr(alerts, "notification_command", lambda *a, **k: (["definitely-not-installed-xyz"], {}))
    assert alerts.notify("t", "m") is False
