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
    for name in ("Frame", "Label", "Button", "Canvas", "Toplevel", "PhotoImage"):
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
