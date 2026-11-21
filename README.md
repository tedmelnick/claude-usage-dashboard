# Claude Usage Dashboard

An always-on-top window (for a second monitor) that shows how much of your Claude plan you've used and when each limit resets. Dark or light, optionally see-through.

Unofficial project. Not affiliated with or endorsed by Anthropic.

## Requirements
- Windows, with Python 3 and tkinter (the python.org installer includes both).
- [Claude Code](https://code.claude.com) installed and signed in once with your claude.ai account (Pro, Max, Team or seat-based Enterprise). The dashboard reads the sign-in Claude Code saves. It does not work with API-key or Console-only logins, or on the Free plan.

## Run
Download the latest release (or clone the repo), then double-click `Start Claude Usage.bat`. Or run `pythonw claude_usage_dashboard.py`.

## What it shows
- Plan limits: current session, weekly all models, and a weekly bar for every model your account reports.
- Reset times: `Resets in 2h 37m (Sat 12:47 AM)`. A reset later today shows only the time.
- Projection under each bar: whether your current pace runs out before the reset, and how early.
- Usage by source this week (Cowork, Claude Code, Chats, and so on), when your account reports it.
- Claude Code history: last 7 days of tokens by model, from local logs.
- Buttons: Open folder, On top, Refresh, Theme (dark / light), Window (Solid, See-through, No background), Clock (12 hr / 24 hr).

Updates every 60 seconds. If an update fails, or 5 minutes pass with no success, the status dot turns amber and a banner explains why.

## Files
- `Preview (demo).bat`: runs with fake data. `python claude_usage_dashboard.py --demo --stale` previews the stale warning.
- `Debug dump.bat`: writes the raw reply to `dump.json`. Use it if bars are missing or wrong.
- `config.json`: created on first close. Holds theme, window mode, clock, refresh interval, the folder for the Open button (`open_folder`, defaults to your home folder), `auto_refresh_token`, and `prices_per_mtok` for an optional cost estimate.

## Privacy and safety
- The dashboard talks only to Anthropic's own usage endpoint (`api.anthropic.com`) and, if you turn on `auto_refresh_token`, Anthropic's token endpoint. Nothing is sent anywhere else.
- It reads Claude Code's `~/.claude/.credentials.json` and local logs on your PC. Never post that file anywhere.
- `auto_refresh_token` is off by default. Turning it on lets the dashboard renew Claude Code's saved sign-in itself, which rewrites that credentials file.

## Notes
- Limits come from an unofficial usage endpoint (the one Claude Code's `/usage` uses). Anthropic can change it without notice and the dashboard would stop working until it's updated.
- If Claude Code's sign-in expires, the banner says so. Open Claude Code once, or set `auto_refresh_token` to `true` in `config.json`. Close the dashboard before editing `config.json`, because closing it re-saves the settings.
- If PowerShell blocks `claude` (scripts disabled), use `claude.cmd` or Command Prompt.
- Projection assumes steady use from the start of the window, so it's an estimate.
- The "No background" window mode needs Windows.

## License
MIT. See `LICENSE`.
