"""Command line: python claude_usage_dashboard.py [--demo] [--stale] [--dump] [--test-alert]."""
import sys

from . import __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(__version__)
        return
    if "--test-alert" in argv:
        from .alerts import notify
        ok = notify("Claude usage alert", "Test notification from the Claude Usage Dashboard.")
        print("Test notification sent." if ok else "Couldn't start a notification on this system.")
        return
    from .gui import run_gui
    run_gui(demo="--demo" in argv, dump="--dump" in argv, stale="--stale" in argv)
