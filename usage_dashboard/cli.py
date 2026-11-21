"""Command line: python claude_usage_dashboard.py [--demo] [--demo-update] [--stale] [--dump] [--test-alert]."""
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
    demo_update = "--demo-update" in argv   # fake demo data plus a pretend newer release
    run_gui(demo="--demo" in argv or demo_update, dump="--dump" in argv, stale="--stale" in argv,
            demo_update=demo_update, argv=argv)
