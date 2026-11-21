"""The window. tkinter is imported inside run_gui(), so the rest of the package
(and the tests) work on machines without it."""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import demo as demo_data
from . import status_icon, taskbar
from .alerts import AlertTracker, notify
from .api import UsageError, fetch_usage
from .config import DUMP_PATH, HISTORY_DAY_CHOICES, HISTORY_DB_PATH, load_config, save_config
from .core import (fmt_secs, fmt_tokens, next_delay, parse_breakdown, parse_limits,
                   project, reply_problem, sub_text, verdict_text)
from .history import (SnapshotStore, history_cost, history_dirs, history_tokens, scan_history,
                      slot_tokens, top_projects)

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
CHIP = {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
        "over": ("#F7C1C1", "#501313")}

PALETTES = {
    "dark": {
        "BG": "#1c1c1b", "PANEL": "#242423", "TRACK": "#2c2c2a", "BORDER": "#3a3a38",
        "TEXT": "#ececea", "TEXT2": "#b8b7b0", "MUTED": "#9a9992",
        "BLUE_D": "#0C447C", "RED_D": "#501313", "AMBER_D": "#412402", "GREEN_D": "#173404",
        "BTN_BORDER": "#55544f", "BANNER_FG": "#FAC775", "BANNER_BG": "#412402",
        "CHIP": {"ok": ("#C0DD97", "#173404"), "warn": ("#FAC775", "#412402"),
                 "over": ("#F7C1C1", "#501313")},
    },
    "light": {
        "BG": "#f6f5f1", "PANEL": "#ffffff", "TRACK": "#e4e3dc", "BORDER": "#d3d2ca",
        "TEXT": "#1f1f1d", "TEXT2": "#4a4944", "MUTED": "#6b6a63",
        "BLUE_D": "#B5D4F4", "RED_D": "#F7C1C1", "AMBER_D": "#FAC775", "GREEN_D": "#C0DD97",
        "BTN_BORDER": "#b5b4ac", "BANNER_FG": "#633806", "BANNER_BG": "#FAEEDA",
        "CHIP": {"ok": ("#27500A", "#EAF3DE"), "warn": ("#633806", "#FAEEDA"),
                 "over": ("#791F1F", "#FCEBEB")},
    },
}
# "No background" makes this one colour see-through (Windows). It is a hair off the
# theme's own background so the text edges blend into it.
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
    globals().update(pal)
    if mode == "none":
        globals()["BG"] = TRANSPARENT_KEY.get(theme, TRANSPARENT_KEY["dark"])


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


def run_gui(demo=False, dump=False, stale=False):
    import tkinter as tk
    from tkinter import font as tkfont

    cfg = load_config()
    if cfg.get("theme") not in PALETTES:
        cfg["theme"] = "dark"
    if cfg.get("window_mode") not in WINDOW_MODES:
        cfg["window_mode"] = "solid"
    apply_palette(cfg["theme"], cfg["window_mode"])
    q = queue.Queue()
    wake = threading.Event()
    state = {"usage": None, "history": None, "trend": None, "last_ok": None, "last_err": None,
             "started": time.time(), "force_at": 0.0, "retry_at": None,
             "alert": None, "alert_until": 0.0}
    cache = {}

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
        while True:
            err_kind, retry_after = None, None
            try:
                days = int(cfg.get("history_days", 7))
                if demo:
                    now = datetime.now(timezone.utc)
                    q.put(("usage", demo_data.demo_usage(now)))
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
                        if store is not None:
                            try:
                                store.record(items)
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
                        q.put(("history", scan_history(history_dirs(), cache, days)))
                    except Exception:
                        pass
            finally:
                refresh = int(cfg.get("refresh_seconds", 60))
                delay = next_delay(refresh, failures, err_kind or "error", retry_after)
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
            tk.Label(row, text=item["label"], font=f_base, fg=TEXT2, bg=BG).pack(side="left")
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
        theme, mode = cfg["theme"], cfg["window_mode"]
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
        set_titlebar(theme == "dark")
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
    root.mainloop()
