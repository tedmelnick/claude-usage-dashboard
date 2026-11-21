"""Maintainer tool: stamp the release time, run the tests, then commit and push.

    python tools/release.py "Add version menu (v2.3.0)"

With the git hook installed (Install hook.bat, once) every commit stamps the time by itself,
so a plain `git commit` is enough; this script then only adds the test run and the push.

Run it from Release.bat. It sets __released__ in usage_dashboard/__init__.py to the current
time (with this PC's UTC offset), runs the tests, then `git add -A`, `git commit` and
`git push`. If the tests fail, the old stamp is put back and nothing is committed.
Publishing the release itself (tag vX.Y.Z) is still done on github.com.
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "usage_dashboard" / "__init__.py"
_STAMP_RE = re.compile(r'^(__released__\s*=\s*)"[^"]*"', re.M)


def stamp(text, now=None):
    """`text` (the __init__.py source) with __released__ set to `now` (default: this moment)."""
    now = now or datetime.now().astimezone()
    new, n = _STAMP_RE.subn(lambda m: '%s"%s"' % (m.group(1), now.isoformat(timespec="seconds")), text, count=1)
    if n != 1:
        raise ValueError("No __released__ line found in usage_dashboard/__init__.py")
    return new


def run(*cmd):
    print("> " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main(argv):
    if argv == ["--stamp-only"]:   # what the git hook runs before every commit
        INIT.write_text(stamp(INIT.read_text(encoding="utf-8")), encoding="utf-8")
        return 0
    message = " ".join(argv).strip() or input("Commit message: ").strip()
    if not message:
        print("No commit message. Nothing was changed.")
        return 1
    if not (ROOT / ".git").exists():
        print("This folder isn't a git clone. Nothing was changed.")
        return 1
    old = INIT.read_text(encoding="utf-8")
    INIT.write_text(stamp(old), encoding="utf-8")
    print("Release time set to " + re.search(r'__released__\s*=\s*"([^"]*)"', INIT.read_text(encoding="utf-8")).group(1))
    probe = subprocess.run([sys.executable, "-m", "pytest", "--version"], cwd=str(ROOT),
                           capture_output=True)
    if probe.returncode == 0:
        if run(sys.executable, "-m", "pytest", "-q") != 0:
            INIT.write_text(old, encoding="utf-8")
            print("The tests failed, so the release time was put back and nothing was committed.")
            return 1
    else:
        print("pytest isn't installed, so the tests were skipped (python -m pip install pytest).")
    for cmd in (("git", "add", "-A"), ("git", "commit", "-m", message), ("git", "push")):
        if run(*cmd) != 0:
            print("Stopped at: " + " ".join(cmd))
            return 1
    print("Pushed. Wait for a green Actions run, then publish the release on github.com.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
