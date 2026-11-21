# Changelog

## 2.0.1
Fixes
- Token refresh no longer fails with a 403. It now uses `platform.claude.com` (Claude Code's current token endpoint), sends the same User-Agent as the usage request, and falls back to the older host. Failures now show the HTTP status.

## 2.0.0
Reliability
- The User-Agent now follows the installed Claude Code version (`user_agent: "auto"`).
- Rate limits (HTTP 429) honour `Retry-After` and back off up to 15 minutes. Other errors back off too, with shorter caps; sign-in problems never do. The banner shows when the next try is.
- If the usage reply changes shape, the banner says so and lists the reply's field names. `--dump` now saves the reply even then.

Features
- Alerts: a desktop notification and an in-window strip when a limit reaches 80% or 90% (configurable) or 100%, when you're on pace to run out before the reset, and when a well-used limit resets. `--test-alert` sends a test.
- Weekly trend: each limit reading is saved to `usage_history.db`, and a chart compares the weekly peak (all models) week over week.
- Top projects: Claude Code token use by project folder.
- History length: 7, 14 or 30 days.
- Compact view with only the plan-limit bars.
- Cost estimate: totals and per-project, once `prices_per_mtok` is filled in (see `prices.example.json`).

Project
- Code split into the `usage_dashboard/` package. `claude_usage_dashboard.py` still starts it, so shortcuts and `.bat` files keep working.
- Test suite and GitHub Actions workflow (Windows and Linux).
- macOS and Linux start script and fallbacks (fonts, notifications, window modes).

## 1.x
Original single-file dashboard.
