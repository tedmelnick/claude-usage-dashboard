import json
import urllib.error

import pytest

from usage_dashboard import api


def test_parse_claude_version():
    assert api.parse_claude_version("2.1.88 (Claude Code)\n") == "2.1.88"
    assert api.parse_claude_version("claude v3.0.1") == "3.0.1"
    assert api.parse_claude_version("no version here") is None
    assert api.parse_claude_version(None) is None


@pytest.fixture(autouse=True)
def reset_cache():
    api._UA_CACHE.update(ua=None, at=0.0)


def test_user_agent_auto_follows_claude_code():
    ua = api.resolve_user_agent({"user_agent": "auto"}, detect=lambda: "2.4.0", now=1000)
    assert ua == "claude-code/2.4.0"


def test_old_shipped_default_counts_as_auto():
    ua = api.resolve_user_agent({"user_agent": "claude-code/2.1.71"}, detect=lambda: "2.4.0", now=1000)
    assert ua == "claude-code/2.4.0"


def test_custom_user_agent_is_respected():
    assert api.resolve_user_agent({"user_agent": "my-tool/1"}, detect=lambda: "9.9.9") == "my-tool/1"


def test_falls_back_when_claude_not_found_and_rechecks_later():
    calls = []

    def detect():
        calls.append(1)
        return None if len(calls) == 1 else "2.5.0"

    assert api.resolve_user_agent({}, detect=detect, now=1000) == api.FALLBACK_UA
    assert api.resolve_user_agent({}, detect=detect, now=1100) == api.FALLBACK_UA   # cached
    assert len(calls) == 1
    later = 1000 + api.UA_RECHECK_SECONDS + 1
    assert api.resolve_user_agent({}, detect=detect, now=later) == "claude-code/2.5.0"


class _Headers(dict):
    pass


def _http_error(code, headers=None):
    return urllib.error.HTTPError("http://x", code, "err", _Headers(headers or {}), None)


@pytest.fixture
def signed_in(monkeypatch):
    far = 4_000_000_000_000
    monkeypatch.setattr(api, "read_credentials",
                        lambda: {"claudeAiOauth": {"accessToken": "t", "expiresAt": far}})
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")


def test_rate_limit_carries_retry_after(monkeypatch, signed_in):
    def boom(*a, **k):
        raise _http_error(429, {"Retry-After": "120"})
    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    with pytest.raises(api.UsageError) as e:
        api.fetch_usage({})
    assert e.value.kind == "rate" and e.value.retry_after == 120


@pytest.mark.parametrize("code,kind", [(401, "auth"), (403, "http"), (500, "http")])
def test_http_errors_are_classified(monkeypatch, signed_in, code, kind):
    def boom(*a, **k):
        raise _http_error(code)
    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    with pytest.raises(api.UsageError) as e:
        api.fetch_usage({})
    assert e.value.kind == kind


def test_network_error_and_success(monkeypatch, signed_in):
    def boom(*a, **k):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    with pytest.raises(api.UsageError) as e:
        api.fetch_usage({})
    assert e.value.kind == "net"

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"limits": []}).encode()

    seen = {}

    def ok(req, timeout=0):
        seen["ua"] = req.get_header("User-agent")
        return Reply()
    monkeypatch.setattr(api.urllib.request, "urlopen", ok)
    assert api.fetch_usage({}) == {"limits": []}
    assert seen["ua"] == "claude-code/test"


def test_missing_sign_in(monkeypatch):
    monkeypatch.setattr(api, "read_credentials", lambda: None)
    with pytest.raises(api.UsageError) as e:
        api.fetch_usage({})
    assert e.value.kind == "auth"


def test_refresh_sends_user_agent_and_falls_back_to_second_endpoint(monkeypatch, tmp_path):
    creds = {"claudeAiOauth": {"accessToken": "old", "refreshToken": "rt", "expiresAt": 0}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")
    monkeypatch.setattr(api, "_creds_path", lambda: tmp_path / ".credentials.json")
    seen = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"access_token": "new", "refresh_token": "rt2", "expires_in": 3600}).encode()

    def fake(req, timeout=0):
        seen.append((req.full_url, req.get_header("User-agent")))
        if len(seen) == 1:
            raise _http_error(403)
        return Reply()
    monkeypatch.setattr(api.urllib.request, "urlopen", fake)
    out = api.refresh_credentials(creds, {})
    assert out["claudeAiOauth"]["accessToken"] == "new"
    assert [u for u, _ in seen] == list(api.TOKEN_URLS)
    assert all(ua == "claude-code/test" for _, ua in seen)


def test_refresh_failure_reports_status(monkeypatch):
    creds = {"claudeAiOauth": {"refreshToken": "rt"}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")
    monkeypatch.setattr(api.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(400)))
    with pytest.raises(api.UsageError) as e:
        api.refresh_credentials(creds, {})
    assert "400" in str(e.value) and e.value.kind == "auth"


def test_refresh_429_backs_off_as_rate_limit(monkeypatch):
    creds = {"claudeAiOauth": {"refreshToken": "rt"}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")
    monkeypatch.setattr(api.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(api.UsageError) as e:
        api.refresh_credentials(creds, {})
    assert e.value.kind == "rate" and e.value.retry_after >= 300
