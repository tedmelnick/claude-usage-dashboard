#!/usr/bin/env python3
"""Claude Usage Dashboard: an always-on-top window for a second monitor.

Shows plan limits (session, weekly, and every per-model weekly quota your account
reports), a projection of whether you will hit each limit before it resets, alerts,
Claude Code token history by model and project, and a week-by-week trend.
Standard library only (tkinter).

Data sources
  * Plan limits: Anthropic's unofficial /api/oauth/usage endpoint, the same one
    Claude Code's /usage command reads. Uses the sign-in token Claude Code saves
    in ~/.claude/.credentials.json. This endpoint is unofficial and may change.
  * History: Claude Code's local logs in ~/.claude/projects/**/*.jsonl.
  * Trend: limit readings the app saves to usage_history.db next to this file.

Run:  pythonw claude_usage_dashboard.py          (no console window)
      python  claude_usage_dashboard.py --demo   (fake data, to preview the look)
      python  claude_usage_dashboard.py --dump   (save the raw API reply to dump.json)
      python  claude_usage_dashboard.py --test-alert   (send a test notification)

The code lives in the usage_dashboard/ folder; this file just starts it.
"""
from usage_dashboard.cli import main

if __name__ == "__main__":
    main()
