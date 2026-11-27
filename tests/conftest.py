import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _private_cooldown_file(monkeypatch, tmp_path):
    """Tests never touch the real saved refresh wait."""
    from usage_dashboard import api
    monkeypatch.setattr(api, "COOLDOWN_PATH", tmp_path / "refresh_cooldown.json")
    api._REFRESH.update(until=0.0, hits=0, loaded=False)
    yield
    api._REFRESH.update(until=0.0, hits=0, loaded=False)
