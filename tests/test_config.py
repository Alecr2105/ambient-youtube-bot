from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.utils.config import PROJECT_ROOT, LicenseType, Mode


def test_defaults_are_safe(make_settings):
    settings = make_settings()
    assert settings.mode is Mode.TEST
    assert settings.allowed_licenses == [LicenseType.CC0, LicenseType.PROCEDURAL, LicenseType.OWN]
    assert not settings.paid_operations_allowed
    assert settings.enable_es_localization is False
    assert settings.declare_synthetic_media is False


def test_env_strings_are_parsed(make_settings, monkeypatch):
    settings = make_settings()
    monkeypatch.setenv("ALLOWED_LICENSES", "CC0, CC-BY-4.0")
    monkeypatch.setenv("ACTIVE_WEEKDAYS", "Mon,fri")
    monkeypatch.setenv("SUBNICHE_WEIGHTS", "sleep:1,study:1,relaxation:2")
    monkeypatch.setenv("VIDEO_DURATION", "")
    parsed = type(settings)(_env_file=None)
    assert parsed.allowed_licenses == [LicenseType.CC0, LicenseType.CC_BY_4]
    assert parsed.active_weekdays == ["mon", "fri"]
    assert parsed.subniche_weights == {"sleep": 0.25, "study": 0.25, "relaxation": 0.5}
    assert parsed.video_duration is None


@pytest.mark.parametrize(
    "override",
    [
        {"allowed_licenses": "CC-BY-NC"},
        {"active_weekdays": "funday"},
        {"subniche_weights": "gaming:1"},
        {"timezone": "Mars/Olympus"},
        {"video_duration_min": 300, "video_duration_max": 200},
        {"fps": 29},
    ],
)
def test_invalid_values_are_rejected(make_settings, override):
    with pytest.raises(ValidationError):
        make_settings(**override)


def test_relative_paths_resolve_to_project_root(make_settings):
    settings = make_settings(output_dir=Path("output"), database_url="sqlite:///data/bot.db")
    assert settings.output_dir == PROJECT_ROOT / "output"
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'data/bot.db').as_posix()}"


def test_secrets_inside_repo_are_flagged(make_settings, tmp_path):
    settings = make_settings(youtube_token_path=PROJECT_ROOT / "youtube.token.json", freesound_token_path=tmp_path / "fs.json")
    assert settings.secrets_inside_repo() == ["youtube_token_path"]
