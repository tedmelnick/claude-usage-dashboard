from usage_dashboard import taskbar


def test_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(taskbar, "_is_windows", lambda: False)
    assert taskbar.set_app_id() is False
    tb = taskbar.TaskbarProgress()
    assert tb.update(1234, 50, "hot") is False
    assert tb.clear(1234) is False


def test_levels_map_to_green_yellow_red():
    assert taskbar.STATE_FOR_LEVEL == {"ok": taskbar.NORMAL, "warn": taskbar.PAUSED,
                                       "hot": taskbar.ERROR}


def test_a_failure_turns_it_off_quietly(monkeypatch):
    monkeypatch.setattr(taskbar, "_is_windows", lambda: True)
    tb = taskbar.TaskbarProgress()
    monkeypatch.setattr(tb, "_connect", lambda: (_ for _ in ()).throw(OSError("no")))
    assert tb.update(1234, 50, "ok") is False
    assert tb.update(1234, 50, "ok") is False   # and it doesn't retry
