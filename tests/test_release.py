import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_tool", Path(__file__).resolve().parent.parent / "tools" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SAMPLE = '"""doc"""\n\n__version__ = "2.3.0"\n__released__ = "2026-11-21T13:15:00-05:00"   # note\n'


def test_stamp_sets_the_time_and_keeps_the_rest():
    now = datetime(2026, 12, 2, 9, 5, 7, tzinfo=timezone(timedelta(hours=-5)))
    out = release.stamp(SAMPLE, now)
    assert '__released__ = "2026-12-02T09:05:07-05:00"   # note' in out
    assert '__version__ = "2.3.0"' in out and out.startswith('"""doc"""')


def test_stamp_defaults_to_now_with_an_offset():
    out = release.stamp(SAMPLE)
    value = out.split('__released__ = "')[1].split('"')[0]
    assert datetime.fromisoformat(value).tzinfo is not None


def test_stamp_needs_the_line():
    with pytest.raises(ValueError):
        release.stamp('__version__ = "1"\n')


def test_the_real_file_can_be_stamped():
    text = release.INIT.read_text(encoding="utf-8")
    assert release.stamp(text) != "" and "__version__" in release.stamp(text)


def test_stamp_only_flag_rewrites_the_file(tmp_path, monkeypatch):
    f = tmp_path / "__init__.py"
    f.write_text(SAMPLE)
    monkeypatch.setattr(release, "INIT", f)
    assert release.main(["--stamp-only"]) == 0
    assert "2026-11-21T13:15:00-05:00" not in f.read_text() and "__released__" in f.read_text()


def test_hook_installs_once_and_leaves_foreign_hooks_alone(tmp_path):
    spec2 = importlib.util.spec_from_file_location(
        "install_hook_tool", Path(__file__).resolve().parent.parent / "tools" / "install_hook.py")
    hook = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(hook)
    assert hook.install(tmp_path)[0] is False                      # not a git clone
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    assert hook.install(tmp_path)[0] is True
    text = (tmp_path / ".git" / "hooks" / "pre-commit").read_bytes()
    assert b"\r" not in text and b"tools/release.py --stamp-only" in text
    assert hook.install(tmp_path)[0] is True                       # running it again is fine
    (tmp_path / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    ok, msg = hook.install(tmp_path)
    assert ok is False and "left alone" in msg
    assert "echo mine" in (tmp_path / ".git" / "hooks" / "pre-commit").read_text()
