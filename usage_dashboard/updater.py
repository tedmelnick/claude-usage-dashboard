"""Checking GitHub for a newer release, and installing it.

Only the standard library is used. The network parts take an `opener` argument so the
tests can stand in for GitHub. Nothing here imports tkinter.
"""
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from . import __version__

REPO = "tedmelnick/claude-usage-dashboard"
RELEASES_URL = "https://api.github.com/repos/%s/releases?per_page=30" % REPO
ARCHIVE_URL = "https://github.com/%s/archive/refs/tags/%%s.zip" % REPO
ALLOWED_HOST_SUFFIXES = ("github.com", "githubusercontent.com")
CHECK_EVERY_SECONDS = 6 * 3600
MAX_ZIP_BYTES = 30 * 1024 * 1024
MAX_UNPACKED_BYTES = 150 * 1024 * 1024
MAX_NOTE_CHARS = 1500

# Never replaced by an update: the user's own data.
PROTECTED = {"config.json", "usage_history.db", "usage_history.db-journal", "dump.json"}
SKIP_DIRS = {".git", "__pycache__", "_backup_before_update", ".pytest_cache"}
BACKUP_DIR = "_backup_before_update"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


class UpdateError(Exception):
    pass


# ---- versions and release notes ----

def parse_version(text):
    """'v2.1.0' -> (2, 1, 0); None for anything that isn't a plain version number."""
    m = _VERSION_RE.match(str(text or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def newer_releases(releases, current):
    """Published, non-draft, non-pre-release releases newer than `current`, newest first."""
    cur = parse_version(current)
    out = []
    for r in releases or []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        v = parse_version(r.get("tag_name"))
        if v is None or cur is None or v <= cur:
            continue
        out.append((v, r))
    out.sort(key=lambda p: p[0], reverse=True)
    return [r for _v, r in out]


def clean_notes(body):
    """GitHub release notes (markdown) as plain text for a small window."""
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        s = line.rstrip()
        s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s)
        s = re.sub(r"^\s*[-*]\s+", "• ", s)
        s = s.replace("**", "").replace("`", "")
        lines.append(s)
    out = "\n".join(lines).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    if len(out) > MAX_NOTE_CHARS:
        out = out[:MAX_NOTE_CHARS].rstrip() + "…"
    return out


def summarize(releases):
    """Just what the window needs from each release."""
    return [{"tag": r.get("tag_name"),
             "date": str(r.get("published_at") or "")[:10],
             "notes": clean_notes(r.get("body"))} for r in releases]


def is_git_clone(app_dir):
    return (Path(app_dir) / ".git").exists()


# ---- asking GitHub ----

def _headers(etag=None):
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "claude-usage-dashboard/%s" % __version__}
    if etag:
        h["If-None-Match"] = etag
    return h


def fetch_releases(etag=None, opener=None):
    """Returns (releases, etag). releases is None when nothing changed since `etag`."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(RELEASES_URL, headers=_headers(etag))
    try:
        with opener(req, timeout=10) as r:
            data = json.loads(r.read())
            new_etag = r.headers.get("ETag") if getattr(r, "headers", None) else None
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        if e.code in (403, 429):
            raise UpdateError("GitHub is limiting requests right now. Try again later.")
        raise UpdateError("GitHub returned HTTP %s." % e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("Can't reach GitHub (%s)." % getattr(e, "reason", e))
    except ValueError:
        raise UpdateError("Unexpected reply from GitHub.")
    if not isinstance(data, list):
        raise UpdateError("Unexpected reply from GitHub.")
    return data, new_etag


def check(current, etag=None, opener=None):
    """One check. Returns (newer, etag): `newer` is summarize() of releases newer than
    `current` (newest first), or None when GitHub says nothing changed since `etag`."""
    releases, new_etag = fetch_releases(etag, opener)
    if releases is None:
        return None, new_etag
    return summarize(newer_releases(releases, current)), new_etag


# ---- installing ----

def _host_ok(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def download_release(tag, dest_dir, opener=None):
    """Download the source zip of release `tag` into dest_dir. Returns the zip's path."""
    if parse_version(tag) is None:
        raise UpdateError("Not a version tag: %r" % (tag,))
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(ARCHIVE_URL % tag, headers=_headers())
    dest = Path(dest_dir) / ("release-%s.zip" % tag)
    try:
        with opener(req, timeout=30) as r:
            final = r.geturl() if hasattr(r, "geturl") else ARCHIVE_URL % tag
            if not _host_ok(final):
                raise UpdateError("The download came from an unexpected host.")
            size = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise UpdateError("The download is larger than expected. Not installing it.")
                    f.write(chunk)
    except UpdateError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("Download failed (%s)." % getattr(e, "reason", e))
    return dest


def _safe_extract(zip_path, out_dir):
    """Unpack the zip, refusing paths that would land outside out_dir."""
    out = Path(out_dir).resolve()
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise UpdateError("The download is larger than expected. Not installing it.")
            target = (out / info.filename).resolve()
            if out != target and out not in target.parents:
                raise UpdateError("The download contains an unsafe path. Not installing it.")
        z.extractall(out)
    roots = [p for p in out.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise UpdateError("The download doesn't look like this app. Not installing it.")
    return roots[0]


def read_version_from(tree):
    """The __version__ inside an unpacked release, or None."""
    try:
        text = (Path(tree) / "usage_dashboard" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return m.group(1) if m else None


def _managed_files(tree):
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            rel = Path(dirpath, name).relative_to(tree)
            if len(rel.parts) == 1 and rel.name in PROTECTED:
                continue
            yield rel


def install_tree(tree, app_dir, tag, old_version=__version__):
    """Copy an unpacked release over app_dir. The files it replaces are backed up first;
    if anything fails, they're put back. Returns the backup folder."""
    tree, app_dir = Path(tree), Path(app_dir)
    for need in ("claude_usage_dashboard.py", "usage_dashboard/__init__.py"):
        if not (tree / need).exists():
            raise UpdateError("The download doesn't look like this app. Not installing it.")
    inside = read_version_from(tree)
    if parse_version(inside) != parse_version(tag):
        raise UpdateError("Release %s reports version %s inside, so it can't be installed. "
                          "Tell the maintainer." % (tag, inside))
    backup = app_dir / BACKUP_DIR / ("v%s" % old_version)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    files = [rel for rel in _managed_files(tree)
             if not (app_dir / rel).exists() or not filecmp.cmp(tree / rel, app_dir / rel, shallow=False)]
    saved, created = [], []
    try:
        for rel in files:
            dest = app_dir / rel
            if dest.exists():
                b = backup / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, b)
                saved.append(rel)
            else:
                created.append(rel)
        for rel in files:
            dest = app_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".update-tmp")
            shutil.copy2(tree / rel, tmp)
            os.replace(tmp, dest)
    except Exception as e:
        for rel in saved:
            try:
                shutil.copy2(backup / rel, app_dir / rel)
            except OSError:
                pass
        for rel in created:
            try:
                (app_dir / rel).unlink()
            except OSError:
                pass
        raise UpdateError("Couldn't install the update (%s). Nothing was changed." % e)
    return backup


def update_to(tag, app_dir, opener=None):
    """Download release `tag` and install it into app_dir. Returns the backup folder."""
    if is_git_clone(app_dir):
        raise UpdateError("This folder is a git clone. Update it with git pull.")
    with tempfile.TemporaryDirectory(prefix="claude-usage-update-") as tmp:
        zip_path = download_release(tag, tmp, opener)
        try:
            tree = _safe_extract(zip_path, Path(tmp) / "unpacked")
        except zipfile.BadZipFile:
            raise UpdateError("The download is damaged. Try again.")
        return install_tree(tree, app_dir, tag)


def relaunch(app_dir, argv=None):
    """Start a fresh copy of the app (call after the old window has closed)."""
    script = str(Path(app_dir) / "claude_usage_dashboard.py")
    args = [sys.executable, script] + [a for a in (argv or []) if a != script]
    kw = {"cwd": str(app_dir), "close_fds": True}
    if sys.platform.startswith("win"):
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, **kw)
        return True
    except OSError:
        return False
