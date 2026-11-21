#!/usr/bin/env sh
# Start the dashboard on macOS or Linux (needs Python 3 with tkinter and a signed-in Claude Code).
cd "$(dirname "$0")" || exit 1
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "tkinter is missing. macOS: brew install python-tk   Debian/Ubuntu: sudo apt install python3-tk"
  exit 1
fi
exec python3 claude_usage_dashboard.py "$@"
