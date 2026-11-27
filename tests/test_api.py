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


def test_refresh_names_the_app_and_falls_back_to_second_endpoint(monkeypatch, tmp_path):
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
    assert all(ua == api.TOKEN_UA for _, ua in seen)


def test_refresh_failure_reports_status(monkeypatch):
    creds = {"claudeAiOauth": {"refreshToken": "rt"}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")
    monkeypatch.setattr(api.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(400)))
    with pytest.raises(api.UsageError) as e:
        api.refresh_credentials(creds, {})
    assert "400" in str(e.value) and e.value.kind == "auth"


@pytest.fixture
def fresh_cooldown():
    api._clear_refresh_cooldown()
    yield
    api._clear_refresh_cooldown()


def _expired(monkeypatch):
    creds = {"claudeAiOauth": {"accessToken": "t", "refreshToken": "rt", "expiresAt": 0}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")
    return creds


def test_refresh_429_starts_a_cooldown_and_does_not_retry(monkeypatch, fresh_cooldown):
    creds = _expired(monkeypatch)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise _http_error(429)
    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    with pytest.raises(api.UsageError) as e:
        api.fetch_usage({"auto_refresh_token": True})
    assert e.value.kind == "wait" and "429" in str(e.value)
    n = len(calls)
    assert api.refresh_cooldown_left() > 0
    # next poll: no network call at all, just the same "wait" notice
    with pytest.raises(api.UsageError) as e2:
        api.fetch_usage({"auto_refresh_token": True})
    assert e2.value.kind == "wait" and len(calls) == n


def test_cooldown_grows_and_honours_retry_after(fresh_cooldown):
    assert api._start_refresh_cooldown(None, now=0) == 300
    assert api._start_refresh_cooldown(None, now=0) == 600
    assert api._start_refresh_cooldown(None, now=0) == 1200
    assert api._start_refresh_cooldown(None, now=0) == 2400
    assert api._start_refresh_cooldown(None, now=0) == 3600
    assert api._start_refresh_cooldown(None, now=0) == 3600       # capped at an hour
    api._clear_refresh_cooldown()
    assert api._start_refresh_cooldown(900, now=0) == 900          # server asked for longer
    assert api.refresh_cooldown_left(now=100) == 800


def test_cooldown_ends_when_claude_code_refreshed_the_token(monkeypatch, fresh_cooldown):
    api._start_refresh_cooldown(None)
    far = 4_000_000_000_000
    monkeypatch.setattr(api, "read_credentials",
                        lambda: {"claudeAiOauth": {"accessToken": "new", "expiresAt": far}})
    monkeypatch.setattr(api, "resolve_user_agent", lambda cfg: "claude-code/test")

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"limits": []}'
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **k: Reply())
    assert api.fetch_usage({"auto_refresh_token": True}) == {"limits": []}
    assert api.refresh_cooldown_left() == 0


def test_successful_refresh_clears_the_cooldown(monkeypatch, tmp_path, fresh_cooldown):
    creds = _expired(monkeypatch)
    monkeypatch.setattr(api, "_creds_path", lambda: tmp_path / ".credentials.json")
    api._start_refresh_cooldown(None)

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"access_token": "new", "expires_in": 3600}).encode()
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **k: Reply())
    api.refresh_credentials(creds, {})
    assert api.refresh_cooldown_left() == 0


def _token_reply(monkeypatch, payload, seen):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def ok(req, timeout=0):
        seen.append(json.loads(req.data))
        return Reply()
    monkeypatch.setattr(api.urllib.request, "urlopen", ok)


def test_refresh_sends_saved_scopes_and_keeps_other_entries(monkeypatch, tmp_path):
    creds = {"claudeAiOauth": {"refreshToken": "rt", "expiresAt": 0, "scopes": ["a:b", "c:d"],
                               "subscriptionType": "max"},
             "mcpOAuth": {"keep": 1}}
    path = tmp_path / ".credentials.json"
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "_creds_path", lambda: path)
    seen = []
    _token_reply(monkeypatch, {"access_token": "new", "refresh_token": "rt2", "expires_in": 3600,
                               "refresh_token_expires_in": 7200}, seen)
    api.refresh_credentials(creds, {})
    assert seen[0]["scope"] == "a:b c:d"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mcpOAuth"] == {"keep": 1}
    oauth = saved["claudeAiOauth"]
    assert oauth["subscriptionType"] == "max" and oauth["refreshToken"] == "rt2"
    assert oauth["refreshTokenExpiresAt"] > oauth["expiresAt"]


def test_refresh_without_saved_scopes_sends_none(monkeypatch, tmp_path):
    creds = {"claudeAiOauth": {"refreshToken": "rt", "expiresAt": 0}}
    monkeypatch.setattr(api, "read_credentials", lambda: creds)
    monkeypatch.setattr(api, "_creds_path", lambda: tmp_path / ".credentials.json")
    seen = []
    _token_reply(monkeypatch, {"access_token": "new"}, seen)
    api.refresh_credentials(creds, {})
    assert "scope" not in seen[0]


def _restart():
    api._REFRESH.update(until=0.0, hits=0, loaded=False)


def test_cooldown_survives_a_restart():
    api._start_refresh_cooldown(None, now=1000)
    _restart()
    assert api.refresh_cooldown_left(now=1100) == 200
    assert api._start_refresh_cooldown(None, now=1400) == 600   # the count carries on


def test_saved_cooldown_is_capped_and_bad_files_are_ignored():
    api.COOLDOWN_PATH.write_text('{"until": 99999999, "hits": 3}', encoding="utf-8")
    _restart()
    assert api.refresh_cooldown_left(now=0) == api.REFRESH_COOLDOWN_MAX
    api.COOLDOWN_PATH.write_text("not json", encoding="utf-8")
    _restart()
    assert api.refresh_cooldown_left(now=0) == 0


def test_clearing_removes_the_saved_cooldown():
    api._start_refresh_cooldown(None)
    assert api.COOLDOWN_PATH.exists()
    api._clear_refresh_cooldown()
    assert not api.COOLDOWN_PATH.exists()
