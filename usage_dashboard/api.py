"""Talking to Anthropic: Claude Code's saved sign-in and the usage endpoint."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import FALLBACK_UA, OLD_DEFAULT_UA

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code's public OAuth client


class UsageError(Exception):
    def __init__(self, msg, kind="error", retry_after=None):
        super().__init__(msg)
        self.kind = kind
        self.retry_after = retry_after


def claude_config_dirs():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return [Path(p.strip()) for p in env.split(",") if p.strip()]
    home = Path.home()
    return [home / ".claude", home / ".config" / "claude"]


def _creds_path():
    for d in claude_config_dirs():
        p = d / ".credentials.json"
        if p.exists():
            return p
    return claude_config_dirs()[0] / ".credentials.json"


def read_credentials():
    try:
        return json.loads(_creds_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_credentials(creds):
    """Refresh the OAuth token and write the rotated tokens back (opt-in)."""
    oauth = dict(creds.get("claudeAiOauth", {}))
    rt = oauth.get("refreshToken")
    if not rt:
        raise UsageError("Sign-in expired. Open Claude Code to sign in again.", "auth")
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": rt,
                       "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        latest = read_credentials()
        if latest and latest.get("claudeAiOauth", {}).get("refreshToken") != rt:
            return latest  # Claude Code refreshed it first
        raise UsageError(f"Token refresh failed ({e.code}). Open Claude Code to refresh it.", "auth")
    oauth["accessToken"] = result["access_token"]
    if result.get("refresh_token"):
        oauth["refreshToken"] = result["refresh_token"]
    oauth["expiresAt"] = int(time.time() * 1000 + int(result.get("expires_in", 3600)) * 1000)
    latest = read_credentials() or creds
    if latest.get("claudeAiOauth", {}).get("refreshToken") != rt:
        return latest
    updated = dict(creds)
    updated["claudeAiOauth"] = oauth
    path = _creds_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(updated), encoding="utf-8")
    os.replace(tmp, path)
    return updated


# ---- user agent: follow the installed Claude Code version ----

_VERSION_RE = re.compile(r"(?<![\d.])(\d+\.\d+\.\d+)(?![\d])")
_UA_CACHE = {"ua": None, "at": 0.0}
UA_RECHECK_SECONDS = 6 * 3600


def parse_claude_version(text):
    """'2.1.88 (Claude Code)' -> '2.1.88'; None if there's no version in the text."""
    m = _VERSION_RE.search(text or "")
    return m.group(1) if m else None


def detect_claude_version():
    """Ask the installed `claude` for its version. None if it can't be found or run."""
    for name in ("claude", "claude.cmd", "claude.exe"):
        exe = shutil.which(name)
        if not exe:
            continue
        kw = {}
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                 timeout=8, **kw)
        except (OSError, subprocess.SubprocessError):
            continue
        v = parse_claude_version(out.stdout)
        if v:
            return v
    return None


def resolve_user_agent(cfg, detect=detect_claude_version, now=None):
    """The User-Agent to send. "auto" (or the old shipped default) follows Claude Code."""
    ua = str(cfg.get("user_agent") or "auto").strip()
    if ua.lower() != "auto" and ua != OLD_DEFAULT_UA:
        return ua
    now = time.time() if now is None else now
    if _UA_CACHE["ua"] is None or now - _UA_CACHE["at"] > UA_RECHECK_SECONDS:
        v = detect()
        _UA_CACHE["ua"] = f"claude-code/{v}" if v else FALLBACK_UA
        _UA_CACHE["at"] = now
    return _UA_CACHE["ua"]


def _retry_after(headers):
    try:
        raw = headers.get("Retry-After") if headers else None
        return int(float(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def fetch_usage(cfg):
    creds = read_credentials()
    if not creds:
        raise UsageError("No Claude Code sign-in found. Run Claude Code once and sign in.", "auth")
    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    if not token:
        raise UsageError("No access token in Claude Code's credentials.", "auth")
    if time.time() * 1000 > oauth.get("expiresAt", 0) - 60_000:
        if cfg.get("auto_refresh_token"):
            creds = refresh_credentials(creds)
            token = creds["claudeAiOauth"]["accessToken"]
        else:
            raise UsageError(
                "Sign-in expired. Open Claude Code once to refresh it, or set "
                "auto_refresh_token to true in config.json.", "auth")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": resolve_user_agent(cfg),
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise UsageError("Sign-in rejected (401). Open Claude Code to refresh it.", "auth")
        if e.code == 429:
            raise UsageError("Rate limited by the usage endpoint.", "rate",
                             retry_after=_retry_after(e.headers))
        if e.code == 403:
            raise UsageError("The usage endpoint refused the request (403). If Claude Code was "
                             "updated, restart the dashboard so it picks up the new version.", "http")
        raise UsageError(f"Usage endpoint returned HTTP {e.code}.", "http")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UsageError(f"Can't reach Anthropic ({getattr(e, 'reason', e)}). Check your connection.", "net")
    except ValueError:
        raise UsageError("Unexpected reply from the usage endpoint.", "format")
