import io
import json
import re
import urllib.error
import zipfile
from pathlib import Path

import pytest

import usage_dashboard
from usage_dashboard import updater


# ---- versions and notes ----

def test_parse_version():
    assert updater.parse_version("v2.1.0") == (2, 1, 0)
    assert updater.parse_version("2.10") == (2, 10, 0)
    assert updater.parse_version("v2.1.0-beta") is None
    assert updater.parse_version("v2.1.0/../x") is None
    assert updater.parse_version(None) is None
    assert updater.parse_version("v2.10.0") > updater.parse_version("v2.9.9")   # not text order


def rel(tag, **kw):
    d = {"tag_name": tag, "published_at": "2026-11-21T15:45:00Z", "body": "- fix", "draft": False,
         "prerelease": False}
    d.update(kw)
    return d


def test_newer_releases_filters_and_sorts():
    releases = [rel("v2.0.1"), rel("v2.2.1"), rel("v2.3.0", draft=True), rel("v2.2.0"),
                rel("v2.4.0", prerelease=True), rel("nightly"), rel("v2.10.0"), rel("v2.1.0")]
    got = [r["tag_name"] for r in updater.newer_releases(releases, "2.2.0")]
    assert got == ["v2.10.0", "v2.2.1"]
    assert updater.newer_releases(releases, "2.10.0") == []
    assert updater.newer_releases([], "2.2.0") == [] and updater.newer_releases(None, "2.2.0") == []
    assert updater.newer_releases(releases, "dev") == []   # unknown own version: offer nothing


def test_clean_notes():
    raw = "## Features\r\n- **Update button** in the `title row`\r\n* second\r\n\r\n\r\n\r\nend"
    assert updater.clean_notes(raw) == ("Features\n• Update button in the title row\n"
                                        "• second\n\nend")
    assert updater.clean_notes(None) == ""
    long = updater.clean_notes("x" * 5000)
    assert len(long) <= updater.MAX_NOTE_CHARS + 1 and long.endswith("…")


def test_summarize():
    out = updater.summarize([rel("v2.2.1", body="### Fixes\n- one")])
    assert out == [{"tag": "v2.2.1", "date": "2026-11-21", "notes": "Fixes\n• one"}]


def test_version_matches_changelog():
    """The version shown in the window must be the newest entry in CHANGELOG.md."""
    text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    top = re.search(r"^## (\d+\.\d+\.\d+)", text, re.M).group(1)
    assert usage_dashboard.__version__ == top


# ---- asking GitHub ----

class FakeResponse:
    def __init__(self, body, headers=None, url="https://api.github.com/x"):
        self._buf = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.headers = headers or {}
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(response=None, error=None, seen=None):
    def opener(req, timeout=None):
        if seen is not None:
            seen.append(req)
        if error:
            raise error
        return response
    return opener


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", {}, None)


def test_fetch_sends_etag_and_user_agent():
    seen = []
    data, etag = updater.fetch_releases(
        "abc", opener_returning(FakeResponse([rel("v2.2.1")], {"ETag": "def"}), seen=seen))
    assert [r["tag_name"] for r in data] == ["v2.2.1"] and etag == "def"
    h = {k.lower(): v for k, v in seen[0].header_items()}
    assert h["if-none-match"] == "abc" and h["user-agent"].startswith("claude-usage-dashboard/")


def test_fetch_not_modified_and_errors():
    assert updater.fetch_releases("abc", opener_returning(error=http_error(304))) == (None, "abc")
    for err in (http_error(403), http_error(500), urllib.error.URLError("offline"), TimeoutError()):
        with pytest.raises(updater.UpdateError):
            updater.fetch_releases(None, opener_returning(error=err))
    with pytest.raises(updater.UpdateError):
        updater.fetch_releases(None, opener_returning(FakeResponse(b"not json")))
    with pytest.raises(updater.UpdateError):
        updater.fetch_releases(None, opener_returning(FakeResponse({"message": "nope"})))


def test_check_returns_only_newer():
    body = [rel("v2.2.1"), rel("v2.2.0"), rel("v2.1.0")]
    newer, etag = updater.check("2.2.0", None, opener_returning(FakeResponse(body, {"ETag": "e1"})))
    assert [r["tag"] for r in newer] == ["v2.2.1"] and etag == "e1"
    newer, _ = updater.check("2.2.0", "e1", opener_returning(error=http_error(304)))
    assert newer is None   # nothing changed since the last answer


# ---- installing ----

def make_release_zip(path, version, extra=None, root="claude-usage-dashboard-"):
    files = {
        "claude_usage_dashboard.py": "print('new launcher')\n",
        "usage_dashboard/__init__.py": f'__version__ = "{version}"\n',
        "usage_dashboard/gui.py": "NEW = True\n",
        "README.md": "new readme\n",
        "config.json": '{"theme": "from the release"}',   # must never overwrite the user's
    }
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(f"{root}{version}/{name}", text)


def make_app(tmp_path, version="2.1.0"):
    app = tmp_path / "app"
    (app / "usage_dashboard").mkdir(parents=True)
    (app / "claude_usage_dashboard.py").write_text("print('old launcher')\n")
    (app / "usage_dashboard" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (app / "usage_dashboard" / "gui.py").write_text("NEW = False\n")
    (app / "config.json").write_text('{"theme": "mine"}')
    (app / "usage_history.db").write_bytes(b"my history")
    (app / "my notes.txt").write_text("keep me")
    return app


def zip_opener(zip_path, url="https://codeload.github.com/x/y/zip/refs/tags/v2.2.0"):
    def opener(req, timeout=None):
        return FakeResponse(Path(zip_path).read_bytes(), url=url)
    return opener


def test_update_installs_and_keeps_user_files(tmp_path):
    app = make_app(tmp_path)
    z = tmp_path / "rel.zip"
    make_release_zip(z, "2.2.0")
    backup = updater.update_to("v2.2.0", app, zip_opener(z))
    assert (app / "usage_dashboard" / "gui.py").read_text() == "NEW = True\n"
    assert (app / "claude_usage_dashboard.py").read_text() == "print('new launcher')\n"
    assert (app / "README.md").read_text() == "new readme\n"          # new file arrives
    assert (app / "config.json").read_text() == '{"theme": "mine"}'    # settings untouched
    assert (app / "usage_history.db").read_bytes() == b"my history"
    assert (app / "my notes.txt").read_text() == "keep me"
    assert (backup / "usage_dashboard" / "gui.py").read_text() == "NEW = False\n"
    assert not list(app.rglob("*.update-tmp"))


def test_update_rolls_back_when_a_copy_fails(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    z = tmp_path / "rel.zip"
    make_release_zip(z, "2.2.0")
    real = updater.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError("file in use")
        return real(src, dst)

    monkeypatch.setattr(updater.os, "replace", flaky)
    with pytest.raises(updater.UpdateError, match="Nothing was changed"):
        updater.update_to("v2.2.0", app, zip_opener(z))
    assert (app / "usage_dashboard" / "gui.py").read_text() == "NEW = False\n"
    assert (app / "claude_usage_dashboard.py").read_text() == "print('old launcher')\n"
    assert not (app / "README.md").exists()   # a file the failed update had created is removed again


def test_update_refuses_a_release_with_the_wrong_version_inside(tmp_path):
    app = make_app(tmp_path)
    z = tmp_path / "rel.zip"
    make_release_zip(z, "2.1.9")   # tagged v2.2.0 but says 2.1.9 inside
    zz = tmp_path / "rel2.zip"
    with zipfile.ZipFile(z) as a, zipfile.ZipFile(zz, "w") as b:
        for info in a.infolist():
            b.writestr(info.filename.replace("2.1.9", "2.2.0"), a.read(info))
    with pytest.raises(updater.UpdateError, match="reports version 2.1.9"):
        updater.update_to("v2.2.0", app, zip_opener(zz))
    assert (app / "usage_dashboard" / "gui.py").read_text() == "NEW = False\n"


def test_update_refuses_bad_downloads(tmp_path):
    app = make_app(tmp_path)
    # a zip that tries to write outside its folder
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("repo-2.2.0/usage_dashboard/__init__.py", '__version__ = "2.2.0"\n')
        z.writestr("../../escaped.txt", "x")
    with pytest.raises(updater.UpdateError, match="unsafe path"):
        updater.update_to("v2.2.0", app, zip_opener(evil))
    assert not (tmp_path / "escaped.txt").exists()
    # not this app at all
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("repo-2.2.0/readme.txt", "hi")
    with pytest.raises(updater.UpdateError, match="doesn't look like this app"):
        updater.update_to("v2.2.0", app, zip_opener(other))
    # not a zip
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"<html>nope</html>")
    with pytest.raises(updater.UpdateError, match="damaged"):
        updater.update_to("v2.2.0", app, zip_opener(junk))
    # downloaded from somewhere other than GitHub
    z = tmp_path / "rel.zip"
    make_release_zip(z, "2.2.0")
    with pytest.raises(updater.UpdateError, match="unexpected host"):
        updater.update_to("v2.2.0", app, zip_opener(z, url="https://evil.example.com/x.zip"))
    assert (app / "usage_dashboard" / "gui.py").read_text() == "NEW = False\n"


def test_download_rejects_odd_tags_and_huge_files(tmp_path, monkeypatch):
    with pytest.raises(updater.UpdateError):
        updater.download_release("v2.2.0/../../x", tmp_path)
    monkeypatch.setattr(updater, "MAX_ZIP_BYTES", 10)
    with pytest.raises(updater.UpdateError, match="larger than expected"):
        updater.download_release("v2.2.0", tmp_path, opener_returning(FakeResponse(b"x" * 100,
                                 url="https://codeload.github.com/x")))


def test_git_clone_is_not_overwritten(tmp_path):
    app = make_app(tmp_path)
    (app / ".git").mkdir()
    assert updater.is_git_clone(app)
    with pytest.raises(updater.UpdateError, match="git"):
        updater.update_to("v2.2.0", app, zip_opener(tmp_path / "never-read.zip"))
    assert (app / "usage_dashboard" / "gui.py").read_text() == "NEW = False\n"
