from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.audio.recipe import load_recipe
from app.database.migrate import upgrade_to_head
from app.database.models import Title, Video
from app.database.session import make_engine, session_scope
from app.licensing.licenses import LicenseRecord
from app.licensing.report import build_license_report
from app.licensing.validator import LicenseValidator
from app.metadata.builder import build_metadata
from app.quality.duplicates import Combination, check_duplicates
from app.quality.gate import license_section, run_gate
from app.quality.metadata import check_metadata
from app.quality.report import QualityReport
from app.utils.config import LicenseType

TODAY = datetime.now(UTC).date()


def package(**changes):
    base = build_metadata(load_recipe("heavy_rain_window"), 240, [], [], seed=1, category_id="10", filmed_in_costa_rica=True)
    return replace(base, **changes)


def failed(report):
    return {c.name for c in report.failures()}


def test_generated_metadata_passes():
    report = check_metadata(package(), contains_synthetic_media=False)
    assert report.passed, failed(report)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"title": "x" * 101}, {"title_length", "upload_body_valid"}),
        ({"title": "HEAVY RAIN ON WINDOW FOR SLEEP 4 HOURS"}, {"title_not_shouting"}),
        ({"title": "Rain 🌧️🌧️ Sounds for Sleep — 4 Hours"}, {"title_emoji"}),
        ({"title": "Rain <Sounds> for Sleep — 4 Hours"}, {"title_characters", "upload_body_valid"}),
        ({"title": "Lluvia para dormir — 4 Hours"}, {"english_only"}),
        ({"description": "short"}, {"description_present"}),
        ({"description": "Great rain ambience for sleeping and studying, see more at https://example.com for other videos of mine."}, {"description_no_external_links"}),
        ({"tags": []}, {"tags_present"}),
        ({"default_language": "es"}, {"languages"}),
    ],
)
def test_metadata_problems_are_caught(changes, expected):
    assert expected <= failed(check_metadata(package(**changes), False))


@pytest.fixture
def engine(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


def combo(title="Heavy Rain on Window for Deep Sleep | 4 Hours", visuals=(1, 2), sounds=("procedural:rain_window", "7")):
    return Combination("heavy_rain_window", tuple(sounds), tuple(visuals), title)


def store(session, video_id, combination, created=None):
    created = created or datetime.now(UTC)
    session.add(Video(id=video_id, mode="test", seed=1, duration_seconds=14400, combination_hash=combination.hash(), target_publish_date=created.date(),
                      concept_fingerprint=combination.fingerprint(), created_at=created))
    session.flush()
    session.add(Title(video_id=video_id, text=combination.title, score=5, selected=True, created_at=created))


def test_hash_ignores_order_and_title_punctuation():
    a = combo(visuals=(2, 1), sounds=("7", "procedural:rain_window"))
    b = combo(title="heavy rain on window for deep sleep 4 hours")
    assert a.hash() == combo().hash() == b.hash()


def test_first_video_passes_duplicate_checks(engine):
    with session_scope(engine) as session:
        assert check_duplicates(session, combo(), TODAY).passed


def test_exact_combination_title_and_concept_repeats_fail(engine):
    with session_scope(engine) as session:
        store(session, "v1", combo())
    with session_scope(engine) as session:
        report = check_duplicates(session, combo(), TODAY)
        assert {"combination_unique", "title_not_near_duplicate", "concept_not_repeated"} <= failed(report)
        assert check_duplicates(session, combo(), TODAY, exclude_video_id="v1").passed  # re-checking itself on resume


def test_near_title_fails_but_new_footage_and_title_pass(engine):
    with session_scope(engine) as session:
        store(session, "v1", combo())
    with session_scope(engine) as session:
        near = check_duplicates(session, combo(title="Heavy Rain on Window for Deep Sleep — 4 Hours!", visuals=(3,)), TODAY)
        assert failed(near) == {"title_not_near_duplicate"}
        fresh = check_duplicates(session, combo(title="Costa Rica Rain on Window for Study & Focus • 3 Hours", visuals=(3,)), TODAY)
        assert fresh.passed


def test_concept_can_return_after_window(engine):
    old = datetime.now(UTC) - timedelta(days=30)
    with session_scope(engine) as session:
        store(session, "v1", combo(title="An Old Title About Rain — 3 Hours"), created=old)
    with session_scope(engine) as session:
        assert check_duplicates(session, combo(title="Costa Rica Heavy Rain for Relaxation • 4 Hours"), TODAY).passed


def test_gate_requires_every_section_and_every_pass():
    ok = [QualityReport(name) for name in ("audio", "video", "metadata", "licenses", "duplicates")]
    for section in ok:
        section.add("fine", True)
    assert run_gate(ok).passed
    assert not run_gate(ok[:-1]).passed
    broken = QualityReport("video")
    broken.add("junction_jumps", False, 2, 0)
    result = run_gate([*ok[:1], broken, *ok[2:]])
    assert not result.passed and "video" in result.failures()


def test_license_section_reflects_validator():
    validator = LicenseValidator([LicenseType.CC0, LicenseType.PROCEDURAL])

    class Res:
        def __init__(self, record):
            self.item, self.kind, self.provider, self.asset_id, self.sound_id, self.local_path, self.license = "x", "texture", "p", "1", None, None, record

    good = build_license_report("r", [Res(LicenseRecord(LicenseType.PROCEDURAL, None, None))], validator)
    bad = build_license_report("r", [Res(LicenseRecord(LicenseType.CC_BY_4, "u", "a", "l"))], validator)
    assert license_section(good).passed
    assert not license_section(bad).passed
