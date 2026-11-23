# Changelog

## 2.4.3
Fixes
- Fixed "Token refresh rate limited (429)" showing again and again. After a 429 the dashboard now stops asking for the new sign-in for 5 minutes, then 10, 20, 40 and at most an hour, instead of trying every few minutes. A longer wait requested by Anthropic is respected.
- While it waits, the dashboard keeps checking Claude Code's sign-in file once a minute, so the numbers come back as soon as Claude Code has refreshed the token (just open Claude Code), with no waiting for the timer.
- The message now explains this and says how to stop the dashboard refreshing on its own (`auto_refresh_token` set to `false`).

## 2.4.2
Fixes
- Fixed a loop where a rate-limited sign-in refresh was retried every minute. It now waits at least five minutes before trying again.

## 2.4.1
Fixes
- Small fixes to the About window.

## 2.4.0
Features
- Seasonal touches: on public holidays and a few observances the window gets a thin colored ring, a strip under the title and a small badge. The red, amber and green status colors never change.
- Quieter refreshing: while nothing changes, the wait between updates grows from 60 to 90 to 120 seconds, and goes back to 60 when a bar moves, on Refresh, on a click in the window, or when a reset is near. The new `refresh_max_seconds` setting controls it (set it equal to `refresh_seconds` to turn it off).
- Your history now also remembers which sources and projects used the most, for the stats to come.
- A few small surprises have been added. Turn them all off with `extras` set to `false` in `config.json`.

Other
- Your own birthday, extra quips and similar personal touches can go in `dates_custom.json` and `quips_custom.json` next to `config.json`. Git ignores them, and updates never replace them.
- `--demo-update` now pretends v2.5.0 and v2.4.1 are out.

## 2.3.0
Features
- Clicking the version next to the title now opens a menu: About this app, What's new, Copy version info, Open release page and Check for updates.
- About this app: what the dashboard is, its version, whether you're running a git clone or a downloaded copy, and a link to the GitHub page.
- What's new: the recent entries of this changelog when you're up to date. When a newer release is waiting, the blue pill still opens the release notes with Update now, Later and Skip this version.
- Copy version info puts the version, install type, Python and OS on the clipboard for a bug report (no file paths or account details).
- Open release page opens this version's page on GitHub.
- Check for updates does what clicking the version used to do. The small check or blue pill next to the version works as before.

Other
- `--demo-update` now pretends v2.4.0 and v2.3.1 are out.

## 2.2.0
Features
- The window shows which version you're running, next to the title (full and compact views).
- A small green check after the version means you're up to date. Click the version to check GitHub right away.
- When a newer release is published, the check turns into a blue pill (for example "↑ v2.3.0"). Click it to read the release notes for every version you're behind, then choose Update now, Later or Skip this version.
- Update now downloads that release from GitHub, keeps your settings and history, backs up the files it replaces (in `_backup_before_update`), and restarts the dashboard. If anything goes wrong it puts the old files back. A copy that is a git clone is never overwritten; update it with `git pull`.
- The app looks for updates when it starts and every 6 hours. Turn that off with `check_for_updates` set to `false`; clicking the version still checks.
- `python claude_usage_dashboard.py --demo-update` previews the update pill and window with made-up releases and installs nothing.

Fixes
- The version number inside the app was still 2.0.0 in releases 2.0.1 and 2.1.0. A test now keeps it in step with this file.

## 2.1.0
Features
- New app icon for the desktop shortcut and the window: a countdown ring around three gauge bars.
- Live taskbar icon (Windows): two bars, current session and weekly, that turn blue, amber or red and show a spark when a limit is urgent (90% used, or on pace to run out).
- Taskbar progress strip showing the more urgent of the two limits, in green, yellow or red.
- `dynamic_icon` and `taskbar_progress` settings (both on by default).
- `Setup.bat` puts the icon on the desktop shortcut.

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
