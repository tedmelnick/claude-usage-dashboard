"""Usage alerts: decide when to warn, and show a desktop notification."""
import os
import subprocess
import sys
from collections import namedtuple

from .core import fmt_dur, project

Alert = namedtuple("Alert", "key kind title message")

RESET_DROP_PCT = 10   # a fall of at least this many points means the window reset
REARM_MARGIN = 2      # a threshold re-arms once usage is this far below it


class AlertTracker:
    """Remembers what has already been announced so each alert fires once.

    Feed it the parsed limits on every successful update; it returns new Alerts.
    The first reading only sets a baseline, so starting the app at 85% doesn't fire
    the 80% alert.
    """

    def __init__(self, thresholds=(80, 90), on_runout=True, on_reset=True, reset_min_pct=50):
        self.levels = sorted(set(int(t) for t in thresholds if 0 < int(t) < 100) | {100})
        self.on_runout = on_runout
        self.on_reset = on_reset
        self.reset_min_pct = reset_min_pct
        self.state = {}

    def evaluate(self, items, now):
        alerts = []
        for it in items:
            key, pct, label = it["key"], it["pct"], it["label"]
            p = project(it, now)
            st = self.state.get(key)
            if st is None:
                self.state[key] = {"pct": pct, "fired": {t for t in self.levels if pct >= t},
                                   "over": p["state"] in ("over", "full")}
                continue
            if self.on_reset and st["pct"] >= self.reset_min_pct and st["pct"] - pct >= RESET_DROP_PCT:
                alerts.append(Alert(key, "reset", "Claude limit reset",
                                    f"{label} has reset. Usage is back to {pct:.0f}%."))
            st["fired"] = {t for t in st["fired"] if pct >= t - REARM_MARGIN}
            newly = [t for t in self.levels if pct >= t and t not in st["fired"]]
            if newly:
                st["fired"].update(newly)
                top = max(newly)
                tail = f" Resets in {fmt_dur(p['remaining_min'])}." if p["remaining_min"] is not None else ""
                if top >= 100:
                    alerts.append(Alert(key, "full", "Claude limit reached",
                                        f"{label} is at 100%.{tail}"))
                else:
                    alerts.append(Alert(key, "threshold", "Claude usage alert",
                                        f"{label} is at {pct:.0f}%.{tail}"))
            runs_out = p["state"] == "over"
            if self.on_runout and runs_out and not st["over"] and pct < 100:
                alerts.append(Alert(key, "runout", "Claude usage on pace to run out",
                                    f"{label}: at this pace you'll run out about "
                                    f"{fmt_dur(p['early_by_min'])} before it resets."))
            st["over"] = runs_out or p["state"] == "full"
            st["pct"] = pct
        return alerts


# ---- desktop notifications ----

# The title and message come in through environment variables, never pasted into the
# script, so they can't be misread as code.
_WIN_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [System.Security.SecurityElement]::Escape($env:CUD_TITLE)
$m = [System.Security.SecurityElement]::Escape($env:CUD_MESSAGE)
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$n = New-Object Windows.UI.Notifications.ToastNotification $x
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""

_MAC_SCRIPT = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
               "-e", "end run"]


def notification_command(title, message, platform=None):
    """(argv, extra_env) for this OS's notifier, or None if there isn't one."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return (["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-Command", _WIN_TOAST],
                {"CUD_TITLE": title, "CUD_MESSAGE": message})
    if platform == "darwin":
        return (["osascript"] + _MAC_SCRIPT + [title, message], {})
    return (["notify-send", "--app-name=Claude Usage", title, message], {})


def notify(title, message, sound=False):
    """Show a desktop notification without waiting for it. True if it was started."""
    cmd = notification_command(title, message)
    ok = False
    if cmd:
        argv, extra = cmd
        kw = {}
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            subprocess.Popen(argv, env=dict(os.environ, **extra), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **kw)
            ok = True
        except OSError:
            ok = False
    if sound and sys.platform.startswith("win"):
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass
    return ok
