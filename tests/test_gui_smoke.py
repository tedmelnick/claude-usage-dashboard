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
    for name in ("Frame", "Label", "Button", "Canvas", "Toplevel", "PhotoImage", "Text", "Menu"):
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
    # CI checks the code out as a git clone, which the updater never overwrites
    from usage_dashboard import updater
    monkeypatch.setattr(updater, "is_git_clone", lambda *_a, **_k: False)
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


def menu_command(label, nth=-1):
    """Click an entry of the version menu that the last click on the version opened."""
    menus = [w for w in Widget.instances if type(w).__name__ == "Menu"]
    assert menus, "the version click opened no menu"
    for name, _a, k in menus[nth].calls:
        if name == "add_command" and k.get("label") == label:
            return k["command"]()
    raise AssertionError(f"no menu entry {label!r}: {[k.get('label') for n, _a, k in menus[nth].calls if n == 'add_command']}")


def open_menu():
    from usage_dashboard import __version__
    click_label(label_with("v" + __version__))


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
    pill = label_with("\u2191 v2.5.0")
    click_label(pill)                       # opens the What's new window
    text_box = [w for w in Widget.instances if type(w).__name__ == "Text"][0]
    shown = " ".join(str(a[1]) for n, a, _k in text_box.calls if n == "insert")
    assert "v2.5.0" in shown and "v2.4.1" in shown and "made-up fix" in shown
    assert "Update now" in buttons() and "Later" in buttons()
    hidden_before = pill.calls.count(("pack_forget", (), {}))
    buttons()["Skip this version"].command()
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["skipped_version"] == "v2.5.0"
    assert pill.calls.count(("pack_forget", (), {})) == hidden_before + 1   # the pill is hidden


def test_update_now_in_preview_installs_nothing(fake_tk):
    from usage_dashboard import gui
    gui.run_gui(demo=True, demo_update=True)
    root = [w for w in Widget.instances if isinstance(w, FakeRoot)][0]
    pump(root)
    settle(root)
    click_label(label_with("\u2191 v2.5.0"))
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
    open_menu()
    menu_command("Check for updates")
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
    open_menu()
    menu_command("Check for updates")
    settle(root, 0.5)
    label_with("Couldn't check")


# ---- the version menu ----

def test_version_menu_has_the_five_entries(fake_tk):
    root = start()
    pump(root)
    pump(root, 0)
    open_menu()
    menu = [w for w in Widget.instances if type(w).__name__ == "Menu"][-1]
    labels_ = [k.get("label") for n, _a, k in menu.calls if n == "add_command"]
    from usage_dashboard import __version__
    assert labels_ == ["About this app", f"What's new in v{__version__}", "Copy version info",
                       "Open release page", "Check for updates"]
    hints = {k["label"]: k["accelerator"] for n, _a, k in menu.calls if n == "add_command" and "accelerator" in k}
    assert hints["Open release page"] == "GitHub"
    assert any(n == "tk_popup" for n, _a, _k in menu.calls)


def test_menu_copy_version_info(fake_tk):
    from usage_dashboard import __version__
    root = start()
    pump(root)
    pump(root, 0)
    open_menu()
    menu_command("Copy version info")
    appended = [a[0] for n, a, _k in root.calls if n == "clipboard_append"]
    assert appended and appended[0].startswith(f"Claude Usage Dashboard v{__version__}")
    assert "Python " in appended[0] and "Updates: " in appended[0]
    label_with("\u2713 Copied")


def test_menu_open_release_page(fake_tk, monkeypatch):
    import webbrowser
    from usage_dashboard import __version__
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    root = start()
    pump(root)
    pump(root, 0)
    open_menu()
    menu_command("Open release page")
    assert opened == [f"https://github.com/tedmelnick/claude-usage-dashboard/releases/tag/v{__version__}"]


def test_menu_release_page_when_no_browser(fake_tk, monkeypatch):
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    root = start()
    pump(root)
    pump(root, 0)
    open_menu()
    menu_command("Open release page")
    label_with("Couldn't open the browser")


def test_menu_about_and_whats_new_when_up_to_date(fake_tk):
    from usage_dashboard import __version__
    root = start()
    pump(root)
    pump(root, 0)
    open_menu()
    menu_command("About this app")
    label_with("Claude Usage Dashboard")
    assert any(str(w.kw.get("text", "")).startswith(f"Version {__version__} \u00b7 released ") and ":" in str(w.kw.get("text", ""))
               for w in labels())
    for key in ("Install", "Runtime", "Settings", "Source", "Updates"):
        label_with(key)
    label_with("github.com/tedmelnick/claude-usage-dashboard")
    label_with("Open folder")
    assert {"Copy version info", "Check for updates", "Close"} <= set(buttons())
    buttons()["Close"].command()               # frees the one dialog slot
    open_menu()
    menu_command(f"What's new in v{__version__}")
    box = [w for w in Widget.instances if type(w).__name__ == "Text"][-1]
    shown = " ".join(str(a[1]) for n, a, _k in box.calls if n == "insert")
    assert f"v{__version__}" in shown
    assert "Open release page" in buttons() and "Update now" not in buttons()


def test_menu_whats_new_with_update_waiting_shows_update_buttons(fake_tk):
    from usage_dashboard import __version__, gui
    gui.run_gui(demo=True, demo_update=True)
    root = [w for w in Widget.instances if isinstance(w, FakeRoot)][0]
    pump(root)
    settle(root)
    open_menu()
    menu_command(f"What's new in v{__version__}")
    assert "Update now" in buttons() and "Skip this version" in buttons()


def test_about_updates_row_follows_the_check(fake_tk):
    from usage_dashboard import gui
    gui.run_gui(demo=True, demo_update=True)
    root = [w for w in Widget.instances if isinstance(w, FakeRoot)][0]
    pump(root)
    settle(root)
    open_menu()
    menu_command("About this app")
    label_with("v2.5.0 available")          # the pretend newer release, in the Updates row
    menu = [w for w in Widget.instances if type(w).__name__ == "Menu"][-1]
    hint = [k["accelerator"] for n, _a, k in menu.calls if n == "add_command" and k.get("label") == "Check for updates"]
    assert hint == ["\u2191 v2.5.0"]        # the menu's right-hand hint shows the waiting update


# ---- extras ----

def handler(root, event, widget=None):
    """The function bound to an event on the root (bind_all) or on a given widget (bind)."""
    src = widget if widget is not None else root
    for name, a, _k in src.calls:
        if name in ("bind", "bind_all") and a and a[0] == event:
            return a[1]
    raise AssertionError(f"nothing bound to {event}")


def key(root, sym):
    handler(root, "<Key>")(types.SimpleNamespace(keysym=sym))


def label_texts():
    return [w.kw.get("text") for w in Widget.instances if type(w).__name__ == "Label"]


def test_key_sequence_switches_the_arcade_theme_on_and_off(fake_tk, tmp_path):
    from usage_dashboard import eggs, gui
    root = start()
    pump(root)
    dark_bg = gui.BG
    for k in eggs.KEY_SEQUENCE:
        key(root, k)
    assert gui.BG == gui.PALETTES["arcade"]["BG"] != dark_bg
    assert gui.BLUE == gui.PALETTES["arcade"]["BLUE"]
    key(root, "Escape")
    assert gui.BG == dark_bg and gui.BLUE == gui.ACCENTS["BLUE"]
    assert not (tmp_path / "config.json").exists() or "arcade" not in (tmp_path / "config.json").read_text()
    for k in eggs.KEY_SEQUENCE:       # the same keys again start it, and again stop it
        key(root, k)
    assert gui.BG != dark_bg
    for k in eggs.KEY_SEQUENCE:
        key(root, k)
    assert gui.BG == dark_bg


def test_typing_opens_the_stats_page(fake_tk):
    root = start()
    pump(root)
    for k in "stats":
        key(root, k)
    assert "Stats" in label_texts()


def test_extras_off_ignores_everything(fake_tk, tmp_path):
    from usage_dashboard import eggs, gui
    (tmp_path / "config.json").write_text('{"extras": false}')
    root = start()
    pump(root)
    before = gui.BG
    for k in eggs.KEY_SEQUENCE:
        key(root, k)
    for k in "stats":
        key(root, k)
    assert gui.BG == before and "Stats" not in label_texts()


def test_seven_clicks_on_the_version_reveal_the_developer_section_in_about(fake_tk):
    root = start()
    pump(root)
    from usage_dashboard import eggs
    # open About through the version menu
    handler(root, "<Button-1>", [w for w in Widget.instances if w.kw.get("text", "").startswith("v2")][0])(
        types.SimpleNamespace(x_root=0, y_root=0))
    menu = [w for w in Widget.instances if type(w).__name__ == "Menu"][-1]
    about = [k["command"] for n, a, k in menu.calls if n == "add_command" and k.get("label") == "About this app"][0]
    about()
    ver = [w for w in Widget.instances if str(w.kw.get("text", "")).startswith("Version ")][0]
    click = handler(root, "<Button-1>", ver)
    for _ in range(7):
        click(types.SimpleNamespace())
    pump(root, 0)
    texts = label_texts()
    assert "Developer panel" in texts and "Secrets found" in texts and "Version" in " ".join(texts)
    assert f"0 of {len(eggs.ALL_IDS)}" not in texts    # opening the panel is itself a find


def test_demo_eggs_preview_runs(fake_tk):
    root = start(argv=["--demo-eggs"])
    pump(root)
    pump(root, 0)


def ring_and_strip():
    ring = [w.kw.get("highlightthickness") for w in Widget.instances
            if type(w).__name__ == "Frame" and w.kw.get("highlightthickness") == 2]
    return bool(ring)


def test_a_holiday_tints_the_window_and_a_normal_day_does_not(fake_tk):
    root = start(argv=["--egg-date=2026-11-26T12:00:00"])      # a Thursday in late November
    pump(root)
    assert ring_and_strip() and "Thanksgiving" in label_texts()
    Widget.instances = []
    root = start(argv=["--egg-date=2026-03-03T12:00:00"])
    pump(root)
    assert not ring_and_strip()


def test_the_quiet_day_is_plain_and_turns_everything_else_off(fake_tk):
    from usage_dashboard import eggs, gui
    root = start(argv=["--egg-date=2026-09-11T12:00:00"])
    pump(root)
    assert not ring_and_strip()
    assert any(str(t).startswith("Remembering") for t in label_texts())
    before = gui.BG
    for k in eggs.KEY_SEQUENCE:
        key(root, k)
    assert gui.BG == before


def test_the_quiet_day_can_be_switched_off(fake_tk, tmp_path):
    (tmp_path / "config.json").write_text('{"quiet_days": false}')
    root = start(argv=["--egg-date=2026-09-11T12:00:00"])
    pump(root)
    assert not any(str(t).startswith("Remembering") for t in label_texts())


def test_spooky_labels_only_on_the_right_day(fake_tk):
    root = start(argv=["--egg-date=2026-11-13T12:00:00"])      # a Friday the 13th
    pump(root)
    assert any(str(t).startswith("Doom") for t in label_texts())
    Widget.instances = []
    root = start(argv=["--egg-date=2026-11-14T12:00:00"])
    pump(root)
    assert not any(str(t).startswith("Doom") for t in label_texts())
