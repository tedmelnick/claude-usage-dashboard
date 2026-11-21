"""The window. tkinter is imported inside run_gui(), so the rest of the package
(and the tests) work on machines without it."""
import json
import math
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __released__, __version__
from . import demo as demo_data
from . import config as config_mod
from . import eggs, status_icon, taskbar, updater
from .alerts import AlertTracker, notify
from .api import UsageError, fetch_usage
from .config import (APP_DIR, CONFIG_PATH, DUMP_PATH, HISTORY_DAY_CHOICES, HISTORY_DB_PATH, load_config,
                     save_config)
from .core import (fmt_secs, fmt_tokens, next_delay, parse_breakdown, parse_limits,
                   project, reply_problem, stretch_delay, sub_text, verdict_text)
from .history import (SnapshotStore, history_cost, history_dirs, history_tokens, read_stats,
                      scan_history, slot_tokens, top_projects)

# Themes. The names below are module globals that apply_palette() swaps at run time;
# the values here are the dark theme (the default).
BG = "#1c1c1b"
PANEL = "#242423"
TRACK = "#2c2c2a"
BORDER = "#3a3a38"
TEXT = "#ececea"
TEXT2 = "#b8b7b0"
MUTED = "#9a9992"
BLUE, BLUE_D = "#378ADD", "#0C447C"
RED, RED_D = "#E24B4A", "#501313"
AMBER, AMBER_D = "#EF9F27", "#412402"
GREEN, GREEN_D = "#639922", "#173404"
TEAL = "#1D9E75"
BTN_BORDER = "#55544f"
BANNER_FG, BANNER_BG = "#FAC775", "#412402"
PILL = ("#ffffff", "#2a63b0")   # (text, background) of the "update available" pill
CHIP = {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
        "over": ("#F7C1C1", "#501313")}

ACCENTS = {"BLUE": BLUE, "RED": RED, "AMBER": AMBER, "GREEN": GREEN, "TEAL": TEAL}

PALETTES = {
    "dark": {
        "BG": "#1c1c1b", "PANEL": "#242423", "TRACK": "#2c2c2a", "BORDER": "#3a3a38",
        "TEXT": "#ececea", "TEXT2": "#b8b7b0", "MUTED": "#9a9992",
        "BLUE_D": "#0C447C", "RED_D": "#501313", "AMBER_D": "#412402", "GREEN_D": "#173404",
        "BTN_BORDER": "#55544f", "BANNER_FG": "#FAC775", "BANNER_BG": "#412402",
        "PILL": ("#ffffff", "#2a63b0"),
        "CHIP": {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
                 "over": ("#F7C1C1", "#501313")},
    },
    "light": {
        "BG": "#f6f5f1", "PANEL": "#ffffff", "TRACK": "#e4e3dc", "BORDER": "#d3d2ca",
        "TEXT": "#1f1f1d", "TEXT2": "#4a4944", "MUTED": "#6b6a63",
        "BLUE_D": "#B5D4F4", "RED_D": "#F7C1C1", "AMBER_D": "#FAC775", "GREEN_D": "#C0DD97",
        "BTN_BORDER": "#b5b4ac", "BANNER_FG": "#633806", "BANNER_BG": "#FAEEDA",
        "PILL": ("#0C447C", "#B5D4F4"),
        "CHIP": {"ok": ("#27500A", "#EAF3DE"), "warn": ("#633806", "#FAEEDA"),
                 "over": ("#791F1F", "#FCEBEB")},
    },
}
# "No background" makes this one colour see-through (Windows). It is a hair off the
# theme's own background so the text edges blend into it.
PALETTES["arcade"] = {
    "BG": "#06061a", "PANEL": "#10102e", "TRACK": "#1b1b44", "BORDER": "#ff2bd6",
    "TEXT": "#eaffff", "TEXT2": "#7df9ff", "MUTED": "#b388ff",
    "BLUE": "#00e5ff", "RED": "#ff2b6b", "AMBER": "#ffd400", "GREEN": "#39ff14", "TEAL": "#00ffb3",
    "BLUE_D": "#00424d", "RED_D": "#4d0a20", "AMBER_D": "#4d4000", "GREEN_D": "#0f4d06",
    "BTN_BORDER": "#ff2bd6", "BANNER_FG": "#ffd400", "BANNER_BG": "#2b1a00",
    "PILL": ("#06061a", "#00e5ff"),
    "CHIP": {"ok": ("#39ff14", "#0f4d06"), "warn": ("#ffd400", "#4d4000"),
             "over": ("#ff2b6b", "#4d0a20")},
}
TRANSPARENT_KEY = {"dark": "#010101", "light": "#fefefe"}
IS_WINDOWS = sys.platform.startswith("win")
# "No background" needs Windows' transparent-colour support.
WINDOW_MODES = ["solid", "glass", "none"] if IS_WINDOWS else ["solid", "glass"]
WINDOW_MODE_LABEL = {"solid": "Solid", "glass": "See-through", "none": "No background"}

FAMILY_COLORS = {
    "fable": "#D4537E",
    "mythos": "#7F77DD",
    "opus": TEAL,
    "sonnet": BLUE,
    "haiku": AMBER,
}
OTHER_COLOR = "#888780"
SOURCE_COLORS = {
    "cowork": "#7F77DD",
    "claude_duet": "#D4537E",
    "claude_code": TEAL,
    "chat": BLUE,
}


def apply_palette(theme, mode):
    """Point the colour globals at the chosen theme. mode "none" uses the see-through key as BG."""
    pal = PALETTES.get(theme) or PALETTES["dark"]
    globals().update(ACCENTS)   # the arcade theme recolours these, so put them back first
    globals().update(pal)
    if mode == "none":
        globals()["BG"] = TRANSPARENT_KEY.get(theme, TRANSPARENT_KEY["dark"])


def readable_on(hex_color):
    """Black or white text, whichever reads better on this colour."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return "#000000"
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def open_folder(path):
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: opens File Explorer at that folder
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


def trend_summary(peaks):
    """One line comparing the open week with the one before it ('' if there's no pair)."""
    if len(peaks) < 2:
        return ""
    cur, prev = peaks[-1], peaks[-2]
    if not cur.get("current"):
        return ""
    diff = cur["peak"] - prev["peak"]
    word = "ahead of" if diff > 0 else "behind" if diff < 0 else "level with"
    gap = f" by {abs(diff):.0f} points" if diff else ""
    return f"This week {cur['peak']:.0f}%, {word} last week's {prev['peak']:.0f}%{gap}"


def run_gui(demo=False, dump=False, stale=False, demo_update=False, argv=None):
    import tkinter as tk
    from tkinter import font as tkfont

    cfg = load_config()
    argv = list(argv or [])
    demo_eggs = "--demo-eggs" in argv
    demo = demo or demo_eggs
    if cfg.get("theme") not in ("dark", "light"):
        cfg["theme"] = "dark"
    if cfg.get("window_mode") not in WINDOW_MODES:
        cfg["window_mode"] = "solid"
    apply_palette(cfg["theme"], cfg["window_mode"])
    q = queue.Queue()
    wake = threading.Event()
    state = {"usage": None, "history": None, "trend": None, "last_ok": None, "last_err": None,
             "started": time.time(), "force_at": 0.0, "retry_at": None,
             "alert": None, "alert_until": 0.0,
             "update": {"status": "unknown", "checking": False, "latest": None,
                        "releases": [], "etag": None},
             "upd_msg": ("", 0.0, ""), "ind_key": "unset", "pill": False, "restart": False}
    cache = {}
    stretch = {"n": 0, "sig": None, "last": 0.0}   # the refresh stretch (see core.stretch_delay)
    egg_clock = {"offset": timedelta(0)}
    for a in argv:   # --egg-date=2026-12-24T23:59:40 previews a date, with the clock running
        if a.startswith("--egg-date="):
            try:
                egg_clock["offset"] = datetime.fromisoformat(a.split("=", 1)[1]) - datetime.now()
            except ValueError:
                pass

    def egg_now():
        return datetime.now() + egg_clock["offset"]

    # ---- worker thread ----
    def worker():
        store = None
        if cfg.get("snapshots_enabled", True) and not demo:
            try:
                store = SnapshotStore(HISTORY_DB_PATH, cfg.get("snapshot_keep_days", 365))
            except Exception:
                store = None  # the trend just stays empty
        tracker = AlertTracker(cfg.get("alert_thresholds") or [], bool(cfg.get("alert_on_runout", True)),
                               bool(cfg.get("alert_on_reset", True)),
                               float(cfg.get("alert_reset_min_pct", 50)))
        failures = 0
        demo_i = 0

        def note_poll(items, now):
            """Count readings that look the same; any change or a near reset starts over."""
            sig = tuple((i["key"], i["pct"]) for i in items)
            soon = any(i["resets_at"] is not None and 0 <= (i["resets_at"] - now).total_seconds() < 600
                       for i in items)
            stretch["n"] = 0 if (sig != stretch["sig"] or soon) else stretch["n"] + 1
            stretch["sig"] = sig
            stretch["last"] = time.time()

        while True:
            err_kind, retry_after = None, None
            try:
                days = int(cfg.get("history_days", 7))
                if demo:
                    now = datetime.now(timezone.utc)
                    if demo_eggs:
                        seq = demo_data.DEMO_EGG_SESSION
                        data = demo_data.demo_usage(now, session=seq[demo_i % len(seq)])
                        demo_i += 1
                    else:
                        data = demo_data.demo_usage(now)
                    note_poll(parse_limits(data), now)
                    q.put(("usage", data))
                    q.put(("history", demo_data.demo_history(days)))
                    q.put(("trend", demo_data.demo_trend(int(cfg.get("trend_weeks", 8)))))
                else:
                    try:
                        data = fetch_usage(cfg)
                        if dump:
                            DUMP_PATH.write_text(json.dumps(data, indent=2),
                                                 encoding="utf-8")
                        problem = reply_problem(data)
                        if problem:
                            raise UsageError(problem, "format")
                        failures = 0
                        q.put(("usage", data))
                        items = parse_limits(data)
                        note_poll(items, datetime.now(timezone.utc))
                        if store is not None:
                            try:
                                store.record(items)
                                weekly = next((i for i in items if i["kind"] == "weekly_all"), None)
                                if weekly is not None:
                                    store.record_sources(parse_breakdown(data), weekly["resets_at"])
                                q.put(("trend", store.window_peaks("weekly_all", int(cfg.get("trend_weeks", 8)))))
                            except Exception:
                                pass
                        if cfg.get("alerts_enabled", True):
                            for a in tracker.evaluate(items, datetime.now(timezone.utc)):
                                notify(a.title, a.message, bool(cfg.get("alert_sound", False)))
                                q.put(("alert", a))
                    except UsageError as e:
                        failures += 1
                        err_kind, retry_after = e.kind, e.retry_after
                        q.put(("error", e))
                    except Exception as e:  # keep the loop alive
                        failures += 1
                        err_kind = "error"
                        q.put(("error", UsageError(f"Unexpected error: {e}")))
                    try:
                        hist = scan_history(history_dirs(), cache, days)
                        q.put(("history", hist))
                        if store is not None:
                            store.record_projects(hist.get("by_project_day"))
                    except Exception:
                        pass
            finally:
                refresh = int(cfg.get("refresh_seconds", 60))
                delay = next_delay(refresh, failures, err_kind or "error", retry_after)
                if demo_eggs:
                    delay = 4      # a quick preview of the sequence
                elif not failures:
                    delay = stretch_delay(refresh, cfg.get("refresh_max_seconds"), stretch["n"])
                q.put(("retry", time.time() + delay if failures else None))
                wake.wait(timeout=delay)
                wake.clear()

    threading.Thread(target=worker, daemon=True).start()

    # ---- window ----
    taskbar.set_app_id()  # so the taskbar shows this app's own icon, not Python's
    root = tk.Tk()
    root.title("Claude usage")
    icon_state = {"key": None, "images": [], "static": None}
    tb_progress = taskbar.TaskbarProgress()

    def set_static_icon():
        assets = Path(__file__).resolve().parent.parent / "assets"
        try:
            if sys.platform.startswith("win") and (assets / "icon.ico").exists():
                root.iconbitmap(default=str(assets / "icon.ico"))
            elif (assets / "icon.png").exists():
                icon_state["static"] = tk.PhotoImage(file=str(assets / "icon.png"))
                root.iconphoto(True, icon_state["static"])
        except Exception:
            pass  # the default icon is fine

    set_static_icon()
    root.configure(bg=BG)
    root.minsize(360, 120)
    families = set(tkfont.families(root))
    face = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans", "Helvetica")
                 if f in families), tkfont.nametofont("TkDefaultFont").actual("family"))
    f_base = tkfont.Font(family=face, size=10)
    f_small = tkfont.Font(family=face, size=9)
    f_bold = tkfont.Font(family=face, size=10, weight="bold")
    f_title = tkfont.Font(family=face, size=12, weight="bold")

    root.attributes("-topmost", bool(cfg.get("always_on_top", True)))
    if cfg.get("window_x") is not None and cfg.get("window_y") is not None:
        root.geometry(f"+{int(cfg['window_x'])}+{int(cfg['window_y'])}")
    root.update()

    def set_titlebar(dark):
        try:  # dark or light title bar on Windows 10/11
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1 if dark else 0)), 4)
        except Exception:
            pass

    outer = tk.Frame(root, bg=BG, padx=16, pady=14)
    outer.pack(fill="both", expand=True)

    # header
    head = tk.Frame(outer, bg=BG)
    head.pack(fill="x")
    lbl_title = tk.Label(head, text="Claude usage", font=f_title, fg=TEXT, bg=BG)
    lbl_title.pack(side="left")
    lbl_ver = tk.Label(head, text="v" + __version__, font=f_small, fg=MUTED, bg=BG, cursor="hand2")
    lbl_ver.pack(side="left", padx=(8, 0), pady=(2, 0))
    lbl_ind = tk.Label(head, text="", font=f_small, fg=MUTED, bg=BG, cursor="hand2")
    lbl_badge = tk.Label(head, text="", font=f_small, fg=TEXT, bg=BG, padx=7, pady=1)
    lbl_streak = tk.Label(head, text="", font=f_small, fg=MUTED, bg=BG)

    def mkbtn(parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd, font=f_small, fg=TEXT, bg=BG,
                      activebackground=TRACK, activeforeground=TEXT, relief="flat",
                      highlightthickness=1, highlightbackground=BTN_BORDER, bd=0,
                      padx=8, pady=3, cursor="hand2")
        return b

    static_btns = []
    btn_refresh = mkbtn(head, "Refresh", lambda: do_refresh())
    btn_view = mkbtn(head, "", lambda: toggle_compact())
    btn_pin = mkbtn(head, "", lambda: toggle_pin())
    btn_folder = mkbtn(head, "Open folder", lambda: do_open())
    # Close button: only shown in "No background" mode, where there is no title bar.
    btn_close = mkbtn(head, "✕", lambda: on_close())

    status = tk.Frame(outer, bg=BG)
    status.pack(fill="x", pady=(4, 10))
    btn_mode = mkbtn(status, "", lambda: cycle_mode())
    btn_theme = mkbtn(status, "", lambda: toggle_theme())
    btn_clock = mkbtn(status, "", lambda: toggle_clock())
    static_btns.extend([btn_refresh, btn_view, btn_pin, btn_folder, btn_close,
                        btn_mode, btn_theme, btn_clock])
    dot = tk.Canvas(status, width=10, height=10, bg=BG, highlightthickness=0)
    dot.pack(side="left")
    dot_id = dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
    lbl_upd = tk.Label(status, text="Starting…", font=f_small, fg=MUTED, bg=BG)
    lbl_upd.pack(side="left", padx=(6, 0))

    strip = tk.Canvas(outer, height=4, bg=BG, highlightthickness=0)
    lbl_line = tk.Label(outer, text="", font=f_small, fg=MUTED, bg=BG, anchor="w", justify="left")

    def layout_buttons():
        """Pack the buttons in order (rightmost first). Compact view keeps only Refresh and View."""
        compact = bool(cfg.get("compact"))
        for b in (btn_close, btn_refresh, btn_view, btn_pin, btn_folder, btn_mode, btn_theme, btn_clock):
            b.pack_forget()
        if root.overrideredirect():
            btn_close.pack(in_=head, side="right", padx=(6, 0))
        btn_refresh.pack(in_=head, side="right", padx=(6, 0))
        btn_view.pack(in_=head, side="right", padx=(6, 0))
        if not compact:
            btn_pin.pack(in_=head, side="right", padx=(6, 0))
            btn_folder.pack(in_=head, side="right")
            btn_mode.pack(in_=status, side="right", padx=(6, 0))
            btn_theme.pack(in_=status, side="right")
            btn_clock.pack(in_=status, side="right", padx=(0, 6))

    banner = tk.Label(outer, text="", font=f_small, fg=BANNER_FG, bg=BANNER_BG, justify="left",
                      anchor="w", wraplength=360, padx=10, pady=8)
    alert_bar = tk.Label(outer, text="", font=f_small, justify="left", anchor="w",
                         wraplength=360, padx=10, pady=8, cursor="hand2")

    body = tk.Frame(outer, bg=BG)
    body.pack(fill="both", expand=True)

    class Bar(tk.Canvas):
        def __init__(self, parent):
            super().__init__(parent, height=10, bg=BG, highlightthickness=0)
            self.vals = (0, 0, BLUE, BLUE_D)
            self.bind("<Configure>", lambda e: self.redraw())

        def set(self, pct, proj, color, ghost):
            self.vals = (pct, proj, color, ghost)
            self.redraw()

        def redraw(self):
            self.delete("all")
            w = max(self.winfo_width(), 10)
            pct, proj, color, ghost = self.vals
            self.create_rectangle(0, 0, w, 10, fill=TRACK, outline="")
            if proj:
                self.create_rectangle(0, 0, w * min(proj, 100) / 100, 10, fill=ghost, outline="")
            self.create_rectangle(0, 0, w * min(pct, 100) / 100, 10, fill=color, outline="")

    def clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def chip(parent, text, kind):
        fg, bg = CHIP.get(kind, (MUTED, TRACK))
        return tk.Label(parent, text=text, font=f_small, fg=fg, bg=bg, padx=8, pady=2)

    def render_limits():
        clear(body)
        usage = state["usage"]
        compact = bool(cfg.get("compact"))
        if usage is None:
            tk.Label(body, text="Waiting for the first update…", font=f_base, fg=MUTED, bg=BG).pack(anchor="w")
            return
        now = datetime.now(timezone.utc)
        items = parse_limits(usage)
        spooky = bool(egg["date"] and egg["date"]["labels"]) and jokes_ok()
        if not items:
            tk.Label(body, text="The usage reply had no limits. Run with --dump to inspect it.",
                     font=f_base, fg=MUTED, bg=BG, wraplength=360, justify="left").pack(anchor="w")
        top = [i for i in items if i["order"] < 2]
        scoped = [i for i in items if i["order"] >= 2]

        def block(item, small=False):
            p = project(item, now)
            pct = item["pct"]
            col = BLUE
            ghost = BLUE_D
            if p["state"] in ("over", "full"):
                col, ghost = RED, RED_D
            elif p["state"] == "warn":
                col, ghost = AMBER, AMBER_D
            elif item["order"] >= 2 and "opus" in item["label"].lower():
                col = TEAL
            fr = tk.Frame(body, bg=BG)
            fr.pack(fill="x", pady=(0, 8 if compact else 10 if small else 14))
            row = tk.Frame(fr, bg=BG)
            row.pack(fill="x")
            label = item["label"]
            if spooky:
                label = eggs.spooky_label(item, egg["texts"])
                found("f13")
            tk.Label(row, text=label, font=f_base, fg=TEXT2, bg=BG).pack(side="left")
            tk.Label(row, text=f"{pct:.0f}%", font=f_bold, fg=TEXT, bg=BG).pack(side="right")
            bar = Bar(fr)
            bar.pack(fill="x", pady=(5, 0))
            proj = p["projected"] if p["state"] in ("ok", "warn", "over") else None
            bar.set(pct, proj if proj and proj > pct else 0, col, ghost)
            st = sub_text(p, item["resets_at"], bool(cfg.get("clock_24h", False)))
            if st:
                tk.Label(fr, text=st, font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(4, 0))
            vt, vk = verdict_text(p)
            if vt and (not (small or compact) or vk in ("over", "warn")):
                chip(fr, vt, vk).pack(anchor="w", pady=(5, 0))

        for it in top:
            block(it)
        if compact:  # per-model bars only when one needs attention
            for it in scoped:
                if project(it, now)["state"] in ("over", "warn", "full"):
                    block(it, small=True)
            return
        if scoped:
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(2, 10))
            for it in scoped:
                block(it, small=True)

        all_rows = parse_breakdown(usage)
        if all_rows:
            zeros = [r for r in all_rows if round(r["pct"]) == 0]
            show_zero = bool(cfg.get("show_zero_sources", False))
            rows = all_rows if show_zero else [r for r in all_rows if round(r["pct"]) > 0]
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(2, 10))
            hdr = tk.Frame(body, bg=BG)
            hdr.pack(fill="x", pady=(0, 6))
            tk.Label(hdr, text="Usage by source, % of weekly limit",
                     font=f_small, fg=MUTED, bg=BG).pack(side="left")
            if zeros:
                mkbtn(hdr, "Hide 0%" if show_zero else f"Show 0% ({len(zeros)})",
                      lambda: toggle_zero()).pack(side="right")
            if not rows:
                tk.Label(body, text="No usage by any source yet this week.", font=f_small,
                         fg=MUTED, bg=BG).pack(anchor="w", pady=(0, 10))
            grid = tk.Frame(body, bg=BG)
            grid.pack(fill="x", pady=(0, 10))
            grid.columnconfigure(1, weight=1)
            for i, r in enumerate(rows):
                tk.Label(grid, text=r["name"], font=f_small, fg=TEXT2, bg=BG, anchor="w",
                         width=13).grid(row=i, column=0, sticky="w", pady=3)
                if round(r["pct"]) == 0 and jokes_ok():
                    tk.Label(grid, text=egg["quips"].get("empty", ""), font=f_small, fg=MUTED, bg=BG,
                             anchor="w").grid(row=i, column=1, sticky="ew", padx=8)
                    found("empty")
                else:
                    bar = Bar(grid)
                    bar.grid(row=i, column=1, sticky="ew", padx=8)
                    bar.set(r["pct"], 0, SOURCE_COLORS.get(r["key"], OTHER_COLOR), TRACK)
                tk.Label(grid, text=f"{r['pct']:.0f}%", font=f_bold, fg=TEXT, bg=BG,
                         anchor="e").grid(row=i, column=2, sticky="e")

    def render_history():
        hist = state["history"]
        if hist is None:
            return
        for w in hist_frame.winfo_children():
            w.destroy()
        days, by_day = hist["days"], hist["by_day"]
        n = len(days)
        fams = []
        for d in days:
            for f in by_day[d]:
                if f not in fams:
                    fams.append(f)
        order = [f for f in ("fable", "mythos", "opus", "sonnet", "haiku", "other") if f in fams]
        totals = [slot_tokens(by_day[d]) for d in days]
        prices = cfg.get("prices_per_mtok") or {}
        row = tk.Frame(hist_frame, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text=f"Claude Code, last {n} days", font=f_base, fg=TEXT2, bg=BG).pack(side="left")
        total_txt = f"{fmt_tokens(sum(totals))} tokens"
        if prices:
            total_txt += f" · ${sum(history_cost(by_day[d], prices) for d in days):.2f} est."
        tk.Label(row, text=total_txt, font=f_bold, fg=TEXT, bg=BG).pack(side="right")
        mkbtn(row, f"{n}d", lambda: cycle_history_days()).pack(side="right", padx=(0, 8))
        cv = tk.Canvas(hist_frame, height=80, bg=BG, highlightthickness=0)
        cv.pack(fill="x", pady=(6, 0))

        def draw(_e=None):
            cv.delete("all")
            w = max(cv.winfo_width(), 100)
            mx = max(totals) or 1
            slot = w / n
            bw = slot * (0.62 if n <= 7 else 0.8)
            step = 1 if n <= 7 else math.ceil(n / 6)
            for i, d in enumerate(days):
                x0 = i * slot + (slot - bw) / 2
                y = 62
                for fam in order:
                    t = by_day[d].get(fam)
                    if not t:
                        continue
                    h = 54 * history_tokens(t) / mx
                    if h < 1:
                        continue
                    cv.create_rectangle(x0, y - h, x0 + bw, y,
                                        fill=FAMILY_COLORS.get(fam, OTHER_COLOR), outline="")
                    y -= h
                if (n - 1 - i) % step == 0:
                    dt = datetime.fromisoformat(d)
                    lbl = dt.strftime("%a")[:2] if n <= 7 else f"{dt.month}/{dt.day}"
                    cv.create_text(i * slot + slot / 2, 73, text=lbl, fill=MUTED, font=f_small)
        cv.bind("<Configure>", draw)
        draw()
        today_slot = by_day[days[-1]]
        txt = f"Today: {fmt_tokens(slot_tokens(today_slot))} tokens"
        if prices:
            txt += f" · ${history_cost(today_slot, prices):.2f} est."
        tk.Label(hist_frame, text=txt, font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(4, 0))
        legend = tk.Frame(hist_frame, bg=BG)
        legend.pack(fill="x", pady=(6, 0))
        for fam in order:
            tot = sum(history_tokens(by_day[d][fam]) for d in days if fam in by_day[d])
            c = tk.Canvas(legend, width=10, height=10, bg=BG, highlightthickness=0)
            c.create_rectangle(0, 0, 10, 10, fill=FAMILY_COLORS.get(fam, OTHER_COLOR), outline="")
            c.pack(side="left", padx=(0, 4))
            tk.Label(legend, text=f"{fam.title()} {fmt_tokens(tot)}", font=f_small, fg=TEXT2,
                     bg=BG).pack(side="left", padx=(0, 12))
        if not order:
            tk.Label(hist_frame, text=f"No Claude Code activity found in the last {n} days.",
                     font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(6, 0))

        rows = top_projects(hist.get("by_project"), int(cfg.get("top_projects", 5)), prices or None)
        if rows:
            tk.Label(hist_frame, text=f"Top projects, last {n} days", font=f_small, fg=MUTED,
                     bg=BG).pack(anchor="w", pady=(12, 4))
            grid = tk.Frame(hist_frame, bg=BG)
            grid.pack(fill="x")
            grid.columnconfigure(1, weight=1)
            mx = rows[0]["tokens"] or 1
            for i, r in enumerate(rows):
                name = r["name"] if len(r["name"]) <= 18 else r["name"][:17] + "…"
                tk.Label(grid, text=name, font=f_small, fg=TEXT2, bg=BG, anchor="w",
                         width=16).grid(row=i, column=0, sticky="w", pady=2)
                bar = Bar(grid)
                bar.grid(row=i, column=1, sticky="ew", padx=8)
                bar.set(100 * r["tokens"] / mx, 0, TEAL, TRACK)
                val = fmt_tokens(r["tokens"])
                if r["cost"] is not None:
                    val += f" · ${r['cost']:.2f}"
                tk.Label(grid, text=val, font=f_small, fg=TEXT, bg=BG,
                         anchor="e").grid(row=i, column=2, sticky="e")

    def render_trend():
        peaks = state["trend"]
        for w in trend_frame.winfo_children():
            w.destroy()
        if peaks is None:
            return
        tk.Label(trend_frame, text="Weekly peak, all models", font=f_base, fg=TEXT2,
                 bg=BG).pack(anchor="w")
        if len(peaks) < 2:
            tk.Label(trend_frame, text="Collecting history. Come back after a week or two of use.",
                     font=f_small, fg=MUTED, bg=BG, wraplength=360, justify="left").pack(anchor="w", pady=(4, 0))
            return
        line = trend_summary(peaks)
        if line:
            tk.Label(trend_frame, text=line, font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(2, 0))
        cv = tk.Canvas(trend_frame, height=92, bg=BG, highlightthickness=0)
        cv.pack(fill="x", pady=(6, 0))

        def draw(_e=None):
            cv.delete("all")
            w = max(cv.winfo_width(), 100)
            n = len(peaks)
            slot = w / n
            bw = slot * 0.62
            step = 1 if n <= 8 else 2
            for i, pk in enumerate(peaks):
                val = pk["peak"]
                h = 56 * min(val, 100) / 100
                col = RED if val >= 100 else AMBER if val >= 80 else BLUE
                x0 = i * slot + (slot - bw) / 2
                cv.create_rectangle(x0, 70 - 56, x0 + bw, 70, fill=TRACK, outline="")
                cv.create_rectangle(x0, 70 - h, x0 + bw, 70, fill=col, outline="")
                if (n - 1 - i) % step == 0:
                    cv.create_text(i * slot + slot / 2, 8, text=f"{val:.0f}", fill=TEXT2, font=f_small)
                    if pk.get("current"):
                        lbl = "now"
                    else:
                        end = datetime.fromtimestamp(pk["resets_at"] - 60)
                        lbl = f"{end.strftime('%b')} {end.day}"
                    cv.create_text(i * slot + slot / 2, 82, text=lbl, fill=MUTED, font=f_small)
        cv.bind("<Configure>", draw)
        draw()

    sep = tk.Frame(outer, bg=BORDER, height=1)
    hist_frame = tk.Frame(outer, bg=BG)
    sep2 = tk.Frame(outer, bg=BORDER, height=1)
    trend_frame = tk.Frame(outer, bg=BG)

    def layout_sections():
        """Full view shows history and the weekly trend under the limits; compact hides them."""
        for w in (sep, hist_frame, sep2, trend_frame):
            w.pack_forget()
        if not cfg.get("compact"):
            sep.pack(fill="x", pady=(4, 12))
            hist_frame.pack(fill="x")
            if demo or cfg.get("snapshots_enabled", True):
                sep2.pack(fill="x", pady=(12, 12))
                trend_frame.pack(fill="x")

    # ---- actions ----
    def update_pin_label():
        btn_pin.config(text="On top: on" if cfg.get("always_on_top", True) else "On top: off")

    def update_view_label():
        btn_view.config(text="View: Compact" if cfg.get("compact") else "View: Full")

    def toggle_pin():
        cfg["always_on_top"] = not cfg.get("always_on_top", True)
        root.attributes("-topmost", bool(cfg["always_on_top"]))
        update_pin_label()
        save_config(cfg)

    def toggle_compact():
        cfg["compact"] = not cfg.get("compact", False)
        save_config(cfg)
        update_view_label()
        layout_buttons()
        layout_sections()
        render_limits()
        render_history()
        render_trend()
        root.geometry("")  # let the window shrink or grow to fit

    def cycle_history_days():
        cur = cfg.get("history_days", 7)
        i = HISTORY_DAY_CHOICES.index(cur) if cur in HISTORY_DAY_CHOICES else 0
        cfg["history_days"] = HISTORY_DAY_CHOICES[(i + 1) % len(HISTORY_DAY_CHOICES)]
        save_config(cfg)
        wake.set()  # rescan now

    def restyle():
        """Recolour the fixed widgets, then rebuild the ones that are redrawn from data."""
        root.configure(bg=BG)
        for w in (outer, head, status, body, hist_frame, trend_frame):
            w.configure(bg=BG)
        lbl_title.configure(fg=TEXT, bg=BG)
        lbl_ver.configure(fg=MUTED, bg=BG)
        lbl_streak.configure(fg=MUTED, bg=BG)
        strip.configure(bg=BG)
        egg["accent"] = None     # colours changed: redraw the seasonal accent and the line
        egg["line"] = None
        tick_eggs()
        state["ind_key"] = "unset"   # colours changed: redraw the check or pill
        refresh_indicator()
        dot.configure(bg=BG)
        lbl_upd.configure(bg=BG, fg=MUTED)
        banner.configure(fg=BANNER_FG, bg=BANNER_BG)
        sep.configure(bg=BORDER)
        sep2.configure(bg=BORDER)
        for b in static_btns:
            b.configure(fg=TEXT, bg=BG, activebackground=TRACK, activeforeground=TEXT,
                        highlightbackground=BTN_BORDER)
        btn_theme.config(text="Theme: " + cfg["theme"].title())
        btn_mode.config(text="Window: " + WINDOW_MODE_LABEL[cfg["window_mode"]])
        btn_clock.config(text="Clock: " + ("24 hr" if cfg.get("clock_24h") else "12 hr"))
        update_view_label()
        show_alert_bar()
        render_limits()
        render_history()
        render_trend()

    def apply_window_mode():
        """Apply theme + window mode (solid / see-through / no background)."""
        theme, mode = ("arcade" if arcade_on() else cfg["theme"]), cfg["window_mode"]
        apply_palette(theme, mode)
        frameless = mode == "none"
        if bool(root.overrideredirect()) != frameless:
            root.update_idletasks()
            rx, ry = root.winfo_rootx(), root.winfo_rooty()
            if frameless:  # remember the title-bar offset so the window doesn't jump
                state["frame_off"] = (rx - root.winfo_x(), ry - root.winfo_y())
                nx, ny = rx, ry
            else:
                ox, oy = state.get("frame_off", (0, 0))
                nx, ny = rx - ox, ry - oy
            root.withdraw()
            root.overrideredirect(frameless)
            root.geometry(f"+{nx}+{ny}")
            root.deiconify()
            root.attributes("-topmost", bool(cfg.get("always_on_top", True)))
        if IS_WINDOWS:
            try:  # Windows: make the background colour see-through
                root.attributes("-transparentcolor", BG if frameless else "")
            except tk.TclError:
                pass
        try:
            alpha = float(cfg.get("glass_alpha", 0.82)) if mode == "glass" else 1.0
            root.attributes("-alpha", min(1.0, max(0.3, alpha)))
        except (tk.TclError, ValueError):
            pass
        layout_buttons()
        root.update_idletasks()
        set_titlebar(theme != "light")
        restyle()

    def toggle_theme():
        cfg["theme"] = "light" if cfg.get("theme") == "dark" else "dark"
        save_config(cfg)
        apply_window_mode()

    def toggle_clock():
        cfg["clock_24h"] = not cfg.get("clock_24h", False)
        btn_clock.config(text="Clock: " + ("24 hr" if cfg["clock_24h"] else "12 hr"))
        save_config(cfg)
        render_limits()

    def cycle_mode():
        cur = cfg.get("window_mode")
        i = WINDOW_MODES.index(cur) if cur in WINDOW_MODES else 0
        cfg["window_mode"] = WINDOW_MODES[(i + 1) % len(WINDOW_MODES)]
        save_config(cfg)
        apply_window_mode()

    # Dragging: with no title bar, drag the window by any empty spot.
    drag = {}

    def drag_start(e):
        if root.overrideredirect() and not isinstance(e.widget, tk.Button):
            drag["dx"], drag["dy"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def drag_move(e):
        if "dx" in drag and root.overrideredirect():
            root.geometry(f"+{e.x_root - drag['dx']}+{e.y_root - drag['dy']}")

    root.bind_all("<ButtonPress-1>", drag_start, add="+")
    root.bind_all("<B1-Motion>", drag_move, add="+")
    root.bind_all("<ButtonRelease-1>", lambda e: drag.clear(), add="+")

    def toggle_zero():
        cfg["show_zero_sources"] = not cfg.get("show_zero_sources", False)
        save_config(cfg)
        render_limits()

    def do_refresh():
        if time.time() - state["force_at"] < 3:
            return
        state["force_at"] = time.time()
        lbl_upd.config(text="Refreshing…")
        wake.set()

    def do_open():
        path = cfg.get("open_folder") or str(Path.home())
        if not open_folder(path):
            state["last_err"] = f"Couldn't open {path}. Check the open_folder path in config.json."

    # ---- alert strip ----
    def show_alert_bar():
        a = state["alert"]
        if not a or time.time() > state["alert_until"]:
            if alert_bar.winfo_ismapped():
                alert_bar.pack_forget()
            return
        kind = "over" if a.kind in ("full", "runout") else "warn"
        fg, bg = CHIP[kind]
        alert_bar.config(text=f"{a.message}  (click to dismiss)", fg=fg, bg=bg)
        if not alert_bar.winfo_ismapped():
            alert_bar.pack(fill="x", pady=(0, 10), before=body)

    def dismiss_alert(_e=None):
        state["alert"] = None
        show_alert_bar()

    alert_bar.bind("<Button-1>", dismiss_alert)

    # ---- updates ----
    def vlabel(tag):
        tag = str(tag or "")
        return tag if tag.startswith("v") else "v" + tag

    def attach_tip(widget, get_text, delay=500):
        """A small hover tip under a widget (after `delay` milliseconds)."""
        tip = {"w": None, "job": None}

        def show():
            tip["job"] = None
            text = get_text()
            if not text:
                return
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
            tk.Label(w, text=text, font=f_small, fg=TEXT, bg=PANEL, padx=8, pady=4,
                     highlightthickness=1, highlightbackground=BORDER).pack()
            tip["w"] = w

        def enter(_e):
            tip["job"] = root.after(delay, show)

        def leave(_e):
            if tip["job"] is not None:
                root.after_cancel(tip["job"])
                tip["job"] = None
            if tip["w"] is not None:
                tip["w"].destroy()
                tip["w"] = None

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<ButtonPress-1>", leave, add="+")

    def pending_update():
        """The newest release to offer, or None (nothing newer, or you skipped it)."""
        latest = state["update"]["latest"]
        return latest if latest and latest != cfg.get("skipped_version") else None

    def refresh_indicator():
        """What sits right after the version: a green check, 'Checking…', or the update pill."""
        refresh_about_status()
        u = state["update"]
        msg, until, kind = state["upd_msg"]
        if u["checking"]:
            key = ("Checking\u2026", MUTED, BG, False)
        elif msg and time.time() < until:
            key = (msg, GREEN if kind == "ok" else MUTED, BG, False)
        elif pending_update():
            key = ("\u2191 " + vlabel(pending_update()), PILL[0], PILL[1], True)
        elif u["status"] == "current":
            key = ("\u2713", GREEN, BG, False)
        else:
            key = None
        state["pill"] = bool(key and key[3])
        if key == state["ind_key"]:
            return
        state["ind_key"] = key
        lbl_ind.pack_forget()
        if key is None:
            return
        text, fg, bg, is_pill = key
        lbl_ind.config(text=text, fg=fg, bg=bg, padx=7 if is_pill else 0, pady=1 if is_pill else 0)
        lbl_ind.pack(in_=head, side="left", padx=(8 if is_pill else 6, 0), pady=(2, 0), after=lbl_ver)

    def run_check(manual=False):
        u = state["update"]
        if u["checking"]:
            return
        u["checking"] = True
        refresh_indicator()

        def job():
            try:
                if demo_update:
                    time.sleep(0.8 if manual else 0)
                    q.put(("update", ("ok", demo_data.demo_releases(), None, manual)))
                elif demo:   # a preview never touches the network
                    q.put(("update", ("ok", [], None, manual)))
                else:
                    newer, etag = updater.check(__version__, u["etag"])
                    q.put(("update", ("ok", newer, etag, manual)))
            except updater.UpdateError as e:
                q.put(("update", ("error", str(e), None, manual)))
            except Exception:
                q.put(("update", ("error", "Couldn't check for updates.", None, manual)))

        threading.Thread(target=job, daemon=True).start()

    def got_check(result):
        status, a, b, manual = result
        u = state["update"]
        u["checking"] = False
        if status == "ok":
            newer, etag = a, b
            if etag:
                u["etag"] = etag
            if newer is not None:
                u["releases"] = newer
                u["latest"] = newer[0]["tag"] if newer else None
            u["status"] = "available" if u["latest"] else "current"
            if manual:
                if u["latest"] and u["latest"] == cfg.get("skipped_version"):
                    cfg["skipped_version"] = ""   # asking again brings a skipped update back
                    save_config(cfg)
                if not u["latest"]:
                    state["upd_msg"] = ("\u2713 Up to date", time.time() + 4, "ok")
        elif manual:
            state["upd_msg"] = ("Couldn't check", time.time() + 4, "err")
        refresh_indicator()

    def auto_check():
        if demo and not demo_update:
            state["update"]["status"] = "current"   # a preview never touches the network
            refresh_indicator()
        elif demo_update or cfg.get("check_for_updates", True):
            run_check(False)
        root.after(updater.CHECK_EVERY_SECONDS * 1000, auto_check)

    def on_indicator_click(_e=None):
        if state["pill"]:
            open_whats_new()
        else:
            run_check(True)

    lbl_ver.bind("<Button-1>", lambda e: open_version_menu(e))
    lbl_ind.bind("<Button-1>", on_indicator_click)
    attach_tip(lbl_ver, lambda: "Click for About, What's new and updates")
    attach_tip(lbl_ind, lambda: (f"{vlabel(pending_update())} is available. Click to see what's new"
                                 if state["pill"] else "You're up to date. Click to check again"))

    dlg = {"w": None}

    def close_dialog():
        state["about_status"] = None
        if dlg["w"] is not None:
            try:
                dlg["w"].destroy()
            except tk.TclError:
                pass
            dlg["w"] = None

    def make_dialog(title):
        """A new small window, or None (after raising the one that is already open)."""
        if dlg["w"] is not None:
            try:
                dlg["w"].lift()
            except tk.TclError:
                pass
            return None
        win = tk.Toplevel(root)
        dlg["w"] = win
        win.title(title)
        win.configure(bg=BG)
        win.transient(root)
        win.geometry(f"+{root.winfo_x() + 30}+{root.winfo_y() + 40}")
        win.protocol("WM_DELETE_WINDOW", close_dialog)
        wrap = tk.Frame(win, bg=BG, padx=16, pady=14)
        wrap.pack(fill="both", expand=True)
        return win, wrap

    def note(text, kind="ok"):
        """A short message right after the version, for a few seconds."""
        state["upd_msg"] = (("\u2713 " if kind == "ok" else "") + text, time.time() + 4, kind)
        refresh_indicator()

    def update_status_text():
        if pending_update():
            return f"{vlabel(pending_update())} available"
        return "up to date" if state["update"]["status"] == "current" else "not checked yet"

    def copy_version_info():
        text = updater.version_info(APP_DIR, getattr(tk, "TkVersion", None), update_status_text())
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()   # keeps the text on the clipboard after the window closes
        except tk.TclError:
            note("Couldn't copy", "err")
            return
        note("Copied")

    def open_in_browser(url):
        try:
            ok = webbrowser.open(url)
        except Exception:
            ok = False
        if not ok:
            note("Couldn't open the browser", "err")

    def open_release_page():
        open_in_browser(updater.release_url(__version__))

    def released_text():
        """'Nov 21, 2026 13:15': the release moment in this PC's own time zone, 24-hour clock."""
        try:
            d = datetime.fromisoformat(__released__).astimezone()
            return f"{d.strftime('%b')} {d.day}, {d.year} {d.strftime('%H:%M')}"
        except (ValueError, OSError, OverflowError):
            return ""

    def check_status_text():
        """'Checking…', 'v2.4.0 available', 'Up to date' or '' (not checked yet)."""
        u = state["update"]
        if u["checking"]:
            return "Checking\u2026"
        if pending_update():
            return f"{vlabel(pending_update())} available"
        return "Up to date" if u["status"] == "current" else ""

    def refresh_about_status():
        lbl = state.get("about_status")
        if lbl is None:
            return
        try:
            lbl.config(text=check_status_text() or "Not checked yet")
        except tk.TclError:
            state["about_status"] = None

    def open_about():
        made = make_dialog("About Claude usage")
        if made is None:
            return
        _win, wrap = made
        link_fg = "#1a5fb4" if cfg.get("theme") == "light" else "#4a9eff"
        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x")
        try:   # the app icon, shrunk to about 60 px
            img = tk.PhotoImage(file=str(APP_DIR / "assets" / "icon.png"))
            img = img.subsample(max(1, round(img.width() / 60)))
            dlg["icon"] = img   # tkinter drops an image nobody holds on to
            icon_lbl = tk.Label(top, image=img, bg=BG)
            icon_lbl.pack(side="left", padx=(0, 14))
            attach_tip(icon_lbl, about_quip, delay=3000)
        except Exception:
            pass
        head_txt = tk.Frame(top, bg=BG)
        head_txt.pack(side="left", anchor="w")
        tk.Label(head_txt, text="Claude Usage Dashboard", font=f_title, fg=TEXT, bg=BG).pack(anchor="w")
        when = released_text()
        ver_lbl = tk.Label(head_txt, text=f"Version {__version__}" + (f" \u00b7 released {when}" if when else ""),
                           font=f_small, fg=MUTED, bg=BG)
        ver_lbl.pack(anchor="w", pady=(2, 0))
        ver_lbl.bind("<Button-1>", lambda e: ver_clicked())

        info = tk.Frame(wrap, bg=BG)
        info.pack(fill="x", pady=(14, 0))
        info.columnconfigure(1, weight=1)
        rows = [0]

        def add_row(key):
            r = rows[0]
            rows[0] += 1
            tk.Label(info, text=key, font=f_small, fg=MUTED, bg=BG, anchor="w").grid(
                row=r, column=0, sticky="nw", pady=3, padx=(0, 18))
            cell = tk.Frame(info, bg=BG)
            cell.grid(row=r, column=1, sticky="ne", pady=3)
            return cell

        def add_text(cell, text, fg=TEXT):
            lbl = tk.Label(cell, text=text, font=f_small, fg=fg, bg=BG, anchor="e", justify="right")
            lbl.pack(side="left")
            return lbl

        def add_link(cell, text, cmd):
            lbl = tk.Label(cell, text=text, font=f_small, fg=link_fg, bg=BG, cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e: cmd())
            return lbl

        if updater.is_git_clone(APP_DIR):
            add_text(add_row("Install"), "git clone (update with git pull)")
        else:
            add_text(add_row("Install"), "downloaded copy (updates in the app)")
        tk_ver = getattr(tk, "TkVersion", "")
        add_text(add_row("Runtime"), f"Python {platform.python_version()}" + (f" \u00b7 Tk {tk_ver}" if tk_ver else ""))
        cell = add_row("Settings")
        add_text(cell, CONFIG_PATH.name + " \u00b7 ")
        add_link(cell, "Open folder", lambda: open_folder(str(CONFIG_PATH.parent)))
        add_link(add_row("Source"), updater.repo_url().replace("https://", ""),
                 lambda: open_in_browser(updater.repo_url()))
        state["about_status"] = add_text(add_row("Updates"), check_status_text() or "Not checked yet")

        tk.Label(wrap, text="Free and open source (MIT). Unofficial: not affiliated with or endorsed by Anthropic.",
                 font=f_small, fg=MUTED, bg=BG, justify="left", anchor="w", wraplength=360
                 ).pack(anchor="w", pady=(8, 0))
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        mkbtn(row, "Copy version info", copy_version_info).pack(side="left")
        mkbtn(row, "Check for updates", lambda: run_check(True)).pack(side="left", padx=(6, 0))
        mkbtn(row, "Close", close_dialog).pack(side="right")

    def open_version_menu(e=None):
        m = tk.Menu(root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=TRACK, activeforeground=TEXT,
                    bd=0, relief="flat", font=f_small)
        status = check_status_text()
        hint = ("\u2713 " + status if status == "Up to date" else
                "\u2191 " + vlabel(pending_update()) if pending_update() else status)
        m.add_command(label="About this app", command=open_about)
        m.add_command(label=f"What's new in {vlabel(__version__)}", command=open_whats_new)
        m.add_command(label="Copy version info", command=copy_version_info)
        m.add_command(label="Open release page", command=open_release_page, accelerator="GitHub")
        m.add_separator()
        m.add_command(label="Check for updates", command=lambda: run_check(True),
                      **({"accelerator": hint} if hint else {}))
        x, y = (e.x_root, e.y_root) if e is not None else (lbl_ver.winfo_rootx(), lbl_ver.winfo_rooty())
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def open_whats_new():
        """The notes for every release you're behind (with Update now), or, when you're up to
        date, the latest entries of CHANGELOG.md."""
        u = state["update"]
        latest = pending_update()
        update_mode = bool(latest and u["releases"])
        made = make_dialog("What's new")
        if made is None:
            return
        _win, wrap = made
        entries = u["releases"] if update_mode else updater.read_changelog(APP_DIR, 3)
        tk.Label(wrap, text="What's new", font=f_title, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(wrap, text=(f"You have v{__version__}. The latest is {vlabel(latest)}." if update_mode
                             else f"You're on v{__version__}. Recent changes:"),
                 font=f_small, fg=MUTED, bg=BG).pack(anchor="w", pady=(2, 10))
        box = tk.Text(wrap, width=58, height=16, wrap="word", bg=PANEL, fg=TEXT2, relief="flat",
                      bd=0, padx=10, pady=8, font=f_small, highlightthickness=1,
                      highlightbackground=BORDER, cursor="arrow")
        box.tag_configure("h", font=f_bold, foreground=TEXT)
        box.tag_configure("d", foreground=MUTED)
        for r in entries:
            box.insert("end", vlabel(r["tag"]), "h")
            box.insert("end", ("   " + r["date"] if r["date"] else "") + "\n", "d")
            box.insert("end", (r["notes"] or "No notes for this release.") + "\n\n")
        if not entries:
            box.insert("end", "No release notes found here. Use Open release page for the notes on GitHub.")
        box.config(state="disabled")
        box.pack(fill="both", expand=True)
        msg = tk.Label(wrap, text="", font=f_small, fg=MUTED, bg=BG, justify="left", anchor="w",
                       wraplength=420)
        msg.pack(fill="x", pady=(8, 0))
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        if not update_mode:
            mkbtn(row, "Close", close_dialog).pack(side="right")
            mkbtn(row, "Open release page", open_release_page).pack(side="right", padx=(0, 6))
            return
        git_clone = updater.is_git_clone(APP_DIR)
        btns = []

        def skip():
            cfg["skipped_version"] = latest
            save_config(cfg)
            close_dialog()
            refresh_indicator()

        def do_update():
            for b in btns:
                b.config(state="disabled")
            msg.config(text="Downloading and installing\u2026", fg=MUTED)
            state["upd_dlg_msg"] = msg
            state["upd_btns"] = btns

            def job():
                try:
                    if demo_update:
                        time.sleep(1.0)
                        raise updater.UpdateError("This is a preview, so nothing was installed.")
                    updater.update_to(latest, APP_DIR)
                    q.put(("update_done", (latest, None)))
                except updater.UpdateError as e:
                    q.put(("update_done", (latest, str(e))))
                except Exception as e:
                    q.put(("update_done", (latest, f"Update failed ({e}). Nothing was changed.")))

            threading.Thread(target=job, daemon=True).start()

        if git_clone:
            msg.config(text="This folder is a git clone, so update it with git pull.")
        else:
            now_btn = mkbtn(row, "Update now", do_update)
            now_btn.config(fg=PILL[0], bg=PILL[1], activebackground=PILL[1], activeforeground=PILL[0],
                           highlightbackground=PILL[1])
            now_btn.pack(side="right")
            btns.append(now_btn)
        later = mkbtn(row, "Later", close_dialog)
        later.pack(side="right", padx=(0, 6))
        skip_btn = mkbtn(row, "Skip this version", skip)
        skip_btn.pack(side="right", padx=(0, 6))
        btns.extend([later, skip_btn])

    def got_update_done(result):
        tag, err = result
        msg, btns = state.get("upd_dlg_msg"), state.get("upd_btns") or []
        if err:
            if msg is not None and dlg["w"] is not None:
                msg.config(text=err, fg=CHIP["over"][0])
            for b in btns:
                b.config(state="normal")
            return
        if msg is not None and dlg["w"] is not None:
            msg.config(text=f"Updated to {vlabel(tag)}. Restarting\u2026", fg=MUTED)
        state["restart"] = True
        root.after(900, on_close)

    # ---- extras: seasonal accents, a few lines, things to find (logic lives in eggs.py) ----
    folder = config_mod.CONFIG_PATH.parent
    egg = {"texts": eggs._load("dates.json", {}), "quips": eggs.load_quips(folder),
           "custom": eggs.load_custom(folder, "dates_custom.json"),
           "store": eggs.EggStore(None if demo else folder / "found.json"),
           "date": None, "flash": ("", 0.0), "last_quip": None, "accent": None, "line": None,
           "strip": [], "streak_on": False, "spooky": None}
    egg["watcher"] = eggs.NumberWatcher(egg["store"], daily_cap=not demo_eggs)
    arcade = {"until": 0.0}
    konami = eggs.SequenceMatcher(eggs.KEY_SEQUENCE)
    typed = eggs.SequenceMatcher(list("stats"))
    title_clicks = eggs.ClickCounter(5)
    version_clicks = eggs.ClickCounter(7)

    def extras_on():
        d = egg["date"]
        return bool(cfg.get("extras", True)) and not (d and d["quiet"])

    def jokes_ok():
        d = egg["date"]
        return extras_on() and not (d and d["calm"])

    def found(egg_id):
        egg["store"].mark(egg_id)

    def toast(text, ms=2600):
        try:
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.geometry(f"+{root.winfo_x() + 30}+{root.winfo_y() + 70}")
            tk.Label(w, text=text, font=f_bold, fg=TEXT, bg=PANEL, padx=14, pady=8,
                     highlightthickness=1, highlightbackground=BORDER).pack()

            def gone():
                try:
                    w.destroy()
                except tk.TclError:
                    pass
            root.after(ms, gone)
        except tk.TclError:
            pass

    def arcade_on():
        return time.time() < arcade["until"]

    def set_arcade_fonts(on):
        fam = next((f for f in ("Consolas", "Courier New", "DejaVu Sans Mono") if f in families), face) if on else face
        for f in (f_base, f_small, f_bold, f_title):
            f.configure(family=fam)

    def start_arcade():
        arcade["until"] = time.time() + 60
        found("konami")
        set_arcade_fonts(True)
        apply_window_mode()

    def stop_arcade():
        arcade["until"] = 0.0
        set_arcade_fonts(False)
        apply_window_mode()

    def current_line():
        """(text, colour) for the line under the title, '' when there is none."""
        now = time.time()
        d = egg["date"]
        if arcade_on():
            return f"{egg['quips'].get('arcade', 'Esc to exit')}  \u00b7  {int(arcade['until'] - now)}s", AMBER
        if jokes_ok() and egg["flash"][1] > now:
            return egg["flash"][0], TEXT
        if d and d["line"] and (d["quiet"] or jokes_ok()):
            found(d["line"][0])
            return d["line"][1], (TEXT2 if d["quiet"] else MUTED)
        return "", MUTED

    def accent_spec():
        d = egg["date"]
        if not d or not cfg.get("extras", True):
            return None
        if d["quiet"]:
            return ("quiet", None, ["#8a8a85"], "")
        h, n = d["holiday"]
        if h:
            return (h["id"], h.get("ring"), list(h.get("strip") or []), eggs.badge_text(h, n), n)
        return None

    def draw_strip(_e=None):
        strip.delete("all")
        cols = egg["strip"]
        w = max(strip.winfo_width(), 10)
        for i, c in enumerate(cols):
            strip.create_rectangle(w * i / len(cols), 0, w * (i + 1) / len(cols), 4, fill=c, outline="")

    strip.bind("<Configure>", draw_strip)

    def apply_accents():
        spec = accent_spec()
        key = (spec, BG)
        if key == egg["accent"]:
            return
        egg["accent"] = key
        ring, cols, badge = (spec[1], spec[2], spec[3]) if spec else (None, [], "")
        outer.configure(highlightthickness=2 if ring else 0, highlightbackground=ring or BG,
                        highlightcolor=ring or BG)
        egg["strip"] = cols
        if cols:
            strip.configure(bg=BG)
            strip.pack(fill="x", before=head, pady=(0, 10))
            draw_strip()
        else:
            strip.pack_forget()
        color = ring or (cols[0] if cols else BG)
        if badge:
            lbl_badge.config(text=badge, bg=color, fg=readable_on(color))
            lbl_badge.pack(in_=head, side="left", padx=(8, 0), pady=(2, 0))
        else:
            lbl_badge.pack_forget()
        if spec and spec[0] != "quiet":
            found("season")

    def tick_eggs():
        """Once a second: today's date state, the accent, the line under the title."""
        try:
            if cfg.get("extras", True):
                egg["date"] = eggs.date_state(egg_now(), egg["custom"], egg["texts"],
                                              bool(cfg.get("quiet_days", True)))
            else:
                egg["date"] = {"line": None, "labels": False, "quiet": False, "calm": False,
                               "holiday": (None, 0)}
            if arcade["until"] and (not arcade_on() or not jokes_ok()):
                stop_arcade()
            apply_accents()
            spooky = (bool(egg["date"]["labels"]) and jokes_ok(), jokes_ok())
            if spooky != egg["spooky"]:
                egg["spooky"] = spooky
                render_limits()
            line = current_line()
            if line != egg["line"]:
                egg["line"] = line
                if line[0]:
                    lbl_line.config(text=line[0], fg=line[1], bg=BG)
                    if not lbl_line.winfo_ismapped():
                        lbl_line.pack(fill="x", before=status, pady=(2, 0))
                elif lbl_line.winfo_ismapped():
                    lbl_line.pack_forget()
            if egg["streak_on"] and not extras_on():
                egg["streak_on"] = False
                update_streak()
        except Exception:
            pass   # these are extras; never let one break the window

    def feed_numbers(usage):
        if (demo and not demo_eggs) or not jokes_ok():
            return
        now = egg_now()
        for it in parse_limits(usage):
            if it["kind"] not in eggs.BARS or it["resets_at"] is None:
                continue
            win = int(round(it["resets_at"].timestamp() / 3600))
            nid = egg["watcher"].feed(it["kind"], it["pct"], win, now)
            if nid:
                egg["flash"] = (eggs.number_line(egg["quips"], nid), time.time() + 10)

    def history_stats():
        return demo_data.demo_stats() if demo else read_stats(HISTORY_DB_PATH)

    def update_streak():
        if egg["streak_on"] and extras_on():
            lbl_streak.config(text=eggs.streak_text(history_stats(), egg["quips"]))
            lbl_streak.pack(in_=head, side="left", padx=(8, 0), pady=(2, 0))
            found("streak")
        else:
            lbl_streak.pack_forget()

    def title_clicked(_e=None):
        if extras_on() and title_clicks.hit():
            egg["streak_on"] = not egg["streak_on"]
            update_streak()

    lbl_title.bind("<Button-1>", title_clicked, add="+")

    def about_quip():
        if not jokes_ok():
            return ""
        q = eggs.pick_quip(egg["quips"], egg["last_quip"])
        egg["last_quip"] = q
        found("hover")
        return q

    def info_rows(wrap, rows):
        grid = tk.Frame(wrap, bg=BG)
        grid.pack(fill="x", pady=(12, 0))
        grid.columnconfigure(1, weight=1)
        for i, (k, v) in enumerate(rows):
            tk.Label(grid, text=k, font=f_small, fg=MUTED, bg=BG, anchor="w").grid(
                row=i, column=0, sticky="nw", pady=3, padx=(0, 18))
            tk.Label(grid, text=v, font=f_small, fg=TEXT, bg=BG, anchor="w", justify="left",
                     wraplength=300).grid(row=i, column=1, sticky="nw", pady=3)

    def open_stats():
        made = make_dialog("Stats")
        if made is None:
            return
        _win, wrap = made
        found("stats")
        tk.Label(wrap, text="Stats", font=f_title, fg=TEXT, bg=BG).pack(anchor="w")
        rows = eggs.stats_rows(history_stats())
        if rows:
            info_rows(wrap, rows)
            tk.Label(wrap, text="All-time means since this history database began.", font=f_small,
                     fg=MUTED, bg=BG, wraplength=380, justify="left").pack(anchor="w", pady=(10, 0))
        else:
            tk.Label(wrap, text="Nothing saved yet. Come back after a few days of use.", font=f_small,
                     fg=MUTED, bg=BG).pack(anchor="w", pady=(8, 0))
        mkbtn(wrap, "Close", close_dialog).pack(anchor="e", pady=(14, 0))

    def open_dev_panel():
        close_dialog()
        made = make_dialog("Developer")
        if made is None:
            return
        _win, wrap = made
        found("dev")
        stats = history_stats()
        tk.Label(wrap, text="Developer", font=f_title, fg=TEXT, bg=BG).pack(anchor="w")
        info_rows(wrap, [
            ("Build time", released_text() or "unknown"),
            ("Commit", eggs.commit_hash(APP_DIR) or "not a git clone"),
            ("Data folder", str(APP_DIR)),
            ("History rows", str(stats.get("rows", 0))),
            ("Secrets found", f"{egg['store'].found_count()} of {len(eggs.ALL_IDS)}"),
        ])
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        mkbtn(row, "Open stats", lambda: (close_dialog(), open_stats())).pack(side="left")
        mkbtn(row, "Open data folder", lambda: open_folder(str(APP_DIR))).pack(side="left", padx=(6, 0))
        mkbtn(row, "Close", close_dialog).pack(side="right")

    def ver_clicked():
        if extras_on() and version_clicks.hit():
            toast(egg["quips"].get("developer", "You're now a developer"))
            root.after(1300, open_dev_panel)

    def on_key(e):
        try:
            if e.keysym == "Escape" and arcade_on():
                stop_arcade()
                return
            if not extras_on():
                return
            if konami.feed(e.keysym) and jokes_ok():
                stop_arcade() if arcade_on() else start_arcade()
            if typed.feed(e.keysym):
                open_stats()
        except Exception:
            pass

    def on_click_activity(_e=None):
        """A click means somebody is looking: start the refresh stretch over, and refresh now
        if the last good reading is older than the normal interval."""
        stretch["n"] = 0
        if (not demo and state["last_err"] is None
                and time.time() - stretch["last"] >= int(cfg.get("refresh_seconds", 60))):
            wake.set()

    root.bind_all("<Key>", on_key, add="+")
    root.bind_all("<ButtonPress-1>", on_click_activity, add="+")

    # ---- ui loop ----
    blink = {"on": False}

    def tick_status():
        now = time.time()
        stale_after = int(cfg.get("stale_after_seconds", 300))
        last_ok = state["last_ok"]
        ref = last_ok if last_ok else state["started"]
        age = now - ref
        # Show the error banner right away if the very first update failed
        is_stale = age > stale_after or (last_ok is None and bool(state["last_err"]))
        if last_ok is None:
            txt = "Waiting for the first update…" if not state["last_err"] else "No update yet"
        elif age < 10:
            txt = "Updated just now"
        elif age < 90:
            txt = f"Updated {int(age)}s ago"
        else:
            txt = f"Updated {int(age // 60)} min ago"
        if is_stale and last_ok is not None:
            txt = f"Last updated {int(age // 60)} min ago"
        if is_stale:
            blink["on"] = not blink["on"]
            dot.itemconfig(dot_id, fill=AMBER if blink["on"] else AMBER_D)
            lbl_upd.config(text=txt, fg=AMBER if blink["on"] else "#BA7517")
            reason = state["last_err"] or f"No update for {int(age // 60)} min."
            if state["retry_at"] and state["retry_at"] > now and state["last_err"]:
                reason += f" Next try in {fmt_secs(state['retry_at'] - now)}."
            banner.config(text=f"{reason} Numbers may be out of date. Press Refresh to retry.")
            if not banner.winfo_ismapped():
                banner.pack(fill="x", pady=(0, 10), before=body)
        else:
            dot.itemconfig(dot_id, fill=GREEN if last_ok else TRACK)
            lbl_upd.config(text=txt, fg=MUTED)
            if banner.winfo_ismapped():
                banner.pack_forget()
        if state["alert"] and time.time() > state["alert_until"]:
            dismiss_alert()
        refresh_indicator()
        tick_eggs()
        root.after(1000, tick_status)

    def toplevel_hwnd():
        try:
            import ctypes
            return ctypes.windll.user32.GetParent(root.winfo_id())
        except Exception:
            return 0

    def update_taskbar():
        """Redraw the taskbar icon (session and weekly bars) and its progress strip."""
        if state["usage"] is None:
            return
        try:
            st = status_icon.summarize(parse_limits(state["usage"]), datetime.now(timezone.utc))
            if st is None:
                return
            if cfg.get("dynamic_icon", True) and st["key"] != icon_state["key"]:
                icon_state["images"] = [tk.PhotoImage(data=status_icon.icon_base64(st, n))
                                        for n in (16, 32, 48)]
                root.iconphoto(True, *icon_state["images"])
                icon_state["key"] = st["key"]
            if cfg.get("taskbar_progress", True):
                tb_progress.update(toplevel_hwnd(), st["progress_pct"], st["progress_state"])
        except Exception:
            pass  # the icon is a nicety; never let it break the window

    def poll():
        changed = False
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "usage":
                    state["usage"] = payload
                    feed_numbers(payload)
                    state["last_ok"] = time.time()
                    state["last_err"] = None
                    if demo and stale:  # preview the stale warning
                        state["last_ok"] = time.time() - 420
                        state["last_err"] = "Can't reach Anthropic (demo)."
                    changed = True
                elif kind == "history":
                    state["history"] = payload
                    render_history()
                elif kind == "trend":
                    state["trend"] = payload
                    render_trend()
                elif kind == "error":
                    state["last_err"] = str(payload)
                elif kind == "retry":
                    state["retry_at"] = payload
                elif kind == "update":
                    got_check(payload)
                elif kind == "update_done":
                    got_update_done(payload)
                elif kind == "alert":
                    state["alert"] = payload
                    state["alert_until"] = time.time() + 15 * 60
                    show_alert_bar()
        except queue.Empty:
            pass
        if changed:
            render_limits()
            update_taskbar()
        root.after(300, poll)

    def redraw_countdowns():
        if state["usage"] is not None:
            render_limits()
            update_taskbar()
        root.after(30_000, redraw_countdowns)

    def on_close():
        x, y = root.winfo_x(), root.winfo_y()
        if root.overrideredirect():  # save the position as if the title bar were there
            ox, oy = state.get("frame_off", (0, 0))
            x, y = root.winfo_rootx() - ox, root.winfo_rooty() - oy
        cfg["window_x"], cfg["window_y"] = x, y
        save_config(cfg)
        root.destroy()

    update_pin_label()
    layout_sections()
    apply_window_mode()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(300, poll)
    root.after(1000, tick_status)
    root.after(30_000, redraw_countdowns)
    root.after(3000, auto_check)
    root.mainloop()
    if state["restart"]:   # an update was installed: start the new version
        updater.relaunch(APP_DIR, [a for a in (argv or []) if a != "--demo-update"])
