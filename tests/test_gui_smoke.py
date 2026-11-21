"""Runs the whole window against a stand-in for tkinter, so the drawing and button code
is exercised on any machine (CI has no display). It checks nothing looks wrong, only that
nothing crashes and that the buttons change the settings they should."""
import sys
import time
import types

import pytest


class Widget:
    instances = []

    def __init__(self, *args, **kw):
        self.args, self.kw = args, kw
        self.command = kw.get("command")
        self.calls = []
        Widget.instances.append(self)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in ("config", "configure"):
            return lambda **k: self.kw.update(k)
        if name == "winfo_width":
            return lambda: 300
        if name in ("winfo_ismapped", "overrideredirect"):
            return lambda *a: False
        if name == "winfo_children":
            return lambda: []
        if name in ("winfo_x", "winfo_y", "winfo_rootx", "winfo_rooty", "winfo_id"):
            return lambda: 0
        return lambda *a, **k: self.calls.append((name, a, k))


class FakeRoot(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.after_calls = []

    def after(self, ms, fn=None):
        self.after_calls.append((ms, fn))

    def mainloop(self):
        pass


@pytest.fixture
def fake_tk(monkeypatch, tmp_path):
    Widget.instances = []
    tk = types.ModuleType("tkinter")
    for name in ("Frame", "Label", "Button", "Canvas", "Toplevel", "PhotoImage", "Text"):
        setattr(tk, name, type(name, (Widget,), {}))
    tk.Tk = FakeRoot
    tk.TclError = type("TclError", (Exception,), {})
    font = types.ModuleType("tkinter.font")
    font.Font = type("Font", (Widget,), {})
    font.families = lambda root=None: ["Segoe UI"]
    font.nametofont = lambda n: types.SimpleNamespace(actual=lambda k: "Arial")
    tk.font = font
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.font", font)
    from usage_dashboard import config
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    return tk


def start(demo=True, **kw):
    from usage_dashboard import gui

    gui.run_gui(demo=demo, **kw)
    return [w for w in Widget.instances if isinstance(w, FakeRoot)][0]


def pump(root, seconds=1.0):
    """Let the background thread deliver data, then run the window's timers once."""
    time.sleep(seconds)
    for _ms, fn in list(root.after_calls):
        fn()


def buttons():
    return {w.kw.get("text"): w for w in Widget.instances if type(w).__name__ == "Button" and w.kw.get("text")}


def test_demo_window_draws(fake_tk):
    root = start()
    pump(root)
    pump(root, 0)


def test_buttons_change_settings(fake_tk, tmp_path):
    import json
    root = start()
    pump(root)
    cfg_file = tmp_path / "config.json"

    def click(label_start):
        for text, b in buttons().items():
            if text.startswith(label_start):
                b.command()
                return
        raise AssertionError(f"no button starting {label_start!r}: {list(buttons())}")

    click("View: Full")
    assert json.loads(cfg_file.read_text())["compact"] is True
    pump(root, 0)
    click("View: Compact")
    assert json.loads(cfg_file.read_text())["compact"] is False
    click("Theme")
    assert json.loads(cfg_file.read_text())["theme"] == "light"
    click("Clock")
    assert json.loads(cfg_file.read_text())["clock_24h"] is True
    click("Window")
    assert json.loads(cfg_file.read_text())["window_mode"] == "glass"
    click("On top")
    pump(root, 0)
    # the history-days button is redrawn with each history update
    for expected in (14, 30, 7):
        click("7d" if expected == 14 else "14d" if expected == 30 else "30d")
        assert json.loads(cfg_file.read_text())["history_days"] == expected
        pump(root, 0.6)  # the worker rescans and the window redraws with the new length


def test_stale_preview_and_compact_start(fake_tk, tmp_path):
    (tmp_path / "config.json").write_text('{"compact": true}')
    root = start(stale=True)
    pump(root)
    pump(root, 0)


def test_taskbar_icon_is_redrawn_from_usage(fake_tk):
    root = start()
    pump(root)
    pump(root, 0)
    names = [c[0] for c in root.calls]
    assert "iconphoto" in names   # the live icon was set from the demo numbers


# ---- version label and update button ----

def settle(root, seconds=0.3):
    """Wait for background threads, then let the window read its queue (and nothing else:
    pump() also re-runs the timers, which would start another update check)."""
    time.sleep(seconds)
    for _ms, fn in list(root.after_calls):
        if fn.__name__ == "poll":
            fn()


def labels():
    return [w for w in Widget.instances if type(w).__name__ == "Label"]


def label_with(text):
    found = [w for w in labels() if w.kw.get("text") == text]
    assert found, f"no label {text!r}: {[w.kw.get('text') for w in labels()]}"
    return found[-1]


def click_label(label):
    for name, a, _k in label.calls:
        if name == "bind" and a[0] == "<Button-1>" and len(a) > 1:
            a[1](None)
            return
    raise AssertionError("label has no click handler")


def test_version_label_and_check_shown(fake_tk):
    from usage_dashboard import __version__
    root = start()
    pump(root)
    pump(root, 0)
    label_with("v" + __version__)
    label_with("\u2713")   # up to date: the green check


def test_update_pill_whats_new_and_skip(fake_tk, tmp_path):
    import json
    from usage_dashboard import gui
    gui.run_gui(demo=True, demo_update=True)
    root = [w for w in Widget.instances if isinstance(w, FakeRoot)][0]
    pump(root)
    settle(root)   # the first pump started the automatic check; this shows its answer
    pill = label_with("\u2191 v2.3.0")
    click_label(pill)                       # opens the What's new window
    text_box = [w for w in Widget.instances if type(w).__name__ == "Text"][0]
    shown = " ".join(str(a[1]) for n, a, _k in text_box.calls if n == "insert")
    assert "v2.3.0" in shown and "v2.2.1" in shown and "made-up fix" in shown
    assert "Update now" in buttons() and "Later" in buttons()
    hidden_before = pill.calls.count(("pack_forget", (), {}))
    buttons()["Skip this version"].command()
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["skipped_version"] == "v2.3.0"
    assert pill.calls.count(("pack_forget", (), {})) == hidden_before + 1   # the pill is hidden


def test_update_now_in_preview_installs_nothing(fake_tk):
    from usage_dashboard import gui
    gui.run_gui(demo=True, demo_update=True)
    root = [w for w in Widget.instances if isinstance(w, FakeRoot)][0]
    pump(root)
    settle(root)
    click_label(label_with("\u2191 v2.3.0"))
    buttons()["Update now"].command()
    settle(root, 1.6)
    assert any("preview" in str(w.kw.get("text", "")) for w in labels())
    assert not root.calls or not any(c[0] == "destroy" for c in root.calls)   # no restart


def test_clicking_the_version_checks_now(fake_tk, tmp_path, monkeypatch):
    """Checks are off in config; clicking the version still checks, and shows the pill."""
    from usage_dashboard import gui, updater
    from usage_dashboard.api import UsageError
    (tmp_path / "config.json").write_text('{"check_for_updates": false, "snapshots_enabled": false}')
    monkeypatch.setattr(gui, "fetch_usage", lambda cfg: (_ for _ in ()).throw(UsageError("offline", "net")))
    calls = []

    def fake_check(current, etag=None, opener=None):
        calls.append(current)
        return [{"tag": "v9.0.0", "date": "2026-12-01", "notes": "big"}], "e1"

    monkeypatch.setattr(updater, "check", fake_check)
    root = start(demo=False)
    pump(root)
    pump(root, 0)
    assert calls == []                      # no automatic check
    from usage_dashboard import __version__
    click_label(label_with("v" + __version__))
    settle(root, 0.5)
    assert calls == [__version__]
    label_with("\u2191 v9.0.0")


def test_automatic_check_and_failed_check(fake_tk, tmp_path, monkeypatch):
    from usage_dashboard import gui, updater
    from usage_dashboard.api import UsageError
    (tmp_path / "config.json").write_text('{"snapshots_enabled": false}')
    monkeypatch.setattr(gui, "fetch_usage", lambda cfg: (_ for _ in ()).throw(UsageError("offline", "net")))
    monkeypatch.setattr(updater, "check", lambda *a, **k: (_ for _ in ()).throw(updater.UpdateError("no")))
    root = start(demo=False)
    pump(root)
    settle(root)
    # a failed automatic check shows nothing new, and the window keeps working
    assert not any(str(w.kw.get("text", "")).startswith("\u2191") for w in labels())
    from usage_dashboard import __version__
    click_label(label_with("v" + __version__))
    settle(root, 0.5)
    label_with("Couldn't check")
