from __future__ import annotations

import pytest

from app.utils.config import Settings


@pytest.fixture
def make_settings(tmp_path, monkeypatch):
    """Settings isolated from the developer's real .env and environment."""

    def factory(**overrides) -> Settings:
        for key in list(Settings.model_fields):
            monkeypatch.delenv(key.upper(), raising=False)
        base = {
            "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            "output_dir": tmp_path / "output",
            "cache_dir": tmp_path / "cache",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
            "visuals_dir": tmp_path / "visuals",
            "audio_own_dir": tmp_path / "audio_own",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)

    return factory
