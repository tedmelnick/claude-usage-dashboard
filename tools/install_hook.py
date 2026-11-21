"""Run once (Install hook.bat): makes every `git commit` in this clone stamp the release time.

The hook (.git/hooks/pre-commit) runs `python tools/release.py --stamp-only` and adds the
changed usage_dashboard/__init__.py to the commit. It lives only in your clone, not in the repo.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "# claude-usage-dashboard: stamp the release time"
HOOK = """#!/bin/sh
%s
cd "$(git rev-parse --show-toplevel)" || exit 0
if python tools/release.py --stamp-only 2>/dev/null || py tools/release.py --stamp-only 2>/dev/null \\
   || python3 tools/release.py --stamp-only 2>/dev/null; then
  git add usage_dashboard/__init__.py
else
  echo "Release time not stamped: couldn't run python." >&2
fi
exit 0
""" % MARKER


def install(root=ROOT):
    hooks = Path(root) / ".git" / "hooks"
    if not hooks.is_dir():
        return False, "This folder isn't a git clone (no .git/hooks)."
    target = hooks / "pre-commit"
    if target.exists() and MARKER not in target.read_text(encoding="utf-8", errors="replace"):
        return False, "A different pre-commit hook is already there, so it was left alone."
    target.write_bytes(HOOK.encode("utf-8"))   # bytes: keeps the line endings as LF for Git's sh
    try:
        target.chmod(0o755)   # macOS and Linux ignore a hook that isn't executable (Windows doesn't care)
    except OSError:
        pass
    return True, "Installed. From now on every commit stamps the release time by itself."


if __name__ == "__main__":
    ok, msg = install()
    print(msg)
    sys.exit(0 if ok else 1)
