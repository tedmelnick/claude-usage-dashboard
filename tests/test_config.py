import json

from usage_dashboard import config
from usage_dashboard.gui import trend_summary


def test_defaults_when_file_missing(tmp_path):
    cfg = config.load_config(tmp_path / "nope.json")
    assert cfg["user_agent"] == "auto" and cfg["history_days"] == 7 and cfg["alerts_enabled"] is True


def test_old_config_keeps_working_and_gets_new_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"refresh_seconds": 90, "user_agent": "claude-code/2.1.71",
                             "theme": "light", "window_x": -944}), encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg["refresh_seconds"] == 90 and cfg["theme"] == "light" and cfg["window_x"] == -944
    assert cfg["compact"] is False and cfg["trend_weeks"] == 8


def test_bad_values_are_cleaned(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"history_days": 99, "top_projects": "many", "trend_weeks": 50,
                             "alert_thresholds": [90, "70", "x", 150, 0, 90]}), encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg["history_days"] == 7
    assert cfg["top_projects"] == 5 and cfg["trend_weeks"] == 8   # one bad number resets the pair
    assert cfg["alert_thresholds"] == [70, 90]


def test_save_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    cfg = config.load_config(p)
    cfg["compact"] = True
    config.save_config(cfg, p)
    assert config.load_config(p)["compact"] is True


def test_trend_summary():
    assert trend_summary([]) == "" and trend_summary([{"peak": 1, "current": True}]) == ""
    peaks = [{"peak": 82, "current": False}, {"peak": 68, "current": True}]
    assert trend_summary(peaks) == "This week 68%, behind last week's 82% by 14 points"
    peaks[1]["peak"] = 90
    assert "ahead of" in trend_summary(peaks)
    peaks[1]["peak"] = 82
    assert trend_summary(peaks) == "This week 82%, level with last week's 82%"
    assert trend_summary([{"peak": 1, "current": False}, {"peak": 2, "current": False}]) == ""


def test_new_settings_have_defaults_and_are_cleaned(tmp_path):
    cfg = config.load_config(tmp_path / "missing.json")
    assert cfg["refresh_max_seconds"] == 120 and cfg["extras"] is True and cfg["quiet_days"] is True
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"refresh_max_seconds": "soon"}))
    assert config.load_config(p)["refresh_max_seconds"] == 120
    p.write_text(json.dumps({"refresh_max_seconds": 3}))
    assert config.load_config(p)["refresh_max_seconds"] == 15
