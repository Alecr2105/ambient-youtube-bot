from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.audio import catalog
from app.audio.generators.granular import GranularTexture, SampleEvent
from app.audio.mixer.render import render_program
from app.audio.providers.base import FetchedSound, ProviderUnavailableError, SoundCandidate
from app.audio.providers.normalize import normalize_to_flac, sha256_file
from app.audio.recipe import load_recipe, instantiate
from app.audio.sources import SourceSelector, SourceUnavailableError
from app.database.migrate import upgrade_to_head
from app.database.session import make_engine, session_scope
from app.licensing.licenses import CANONICAL_URLS, LicenseRecord, normalize_license
from app.licensing.report import build_license_report
from app.licensing.validator import LicenseValidator
from app.quality.audio import AudioThresholds, check_audio
from app.utils.config import LicenseType

SR = 48000


def texture_file(path: Path, seconds: float, seed: int) -> Path:
    rng = np.random.default_rng(seed)
    # Slow (1 s) level changes: texture-like, not speech-like.
    steps = rng.uniform(0.5, 1.0, int(seconds) + 2)
    envelope = np.interp(np.arange(int(SR * seconds)) / SR, np.arange(len(steps)), steps)
    audio = rng.standard_normal((int(SR * seconds), 2)) * 0.1 * envelope[:, None]
    sf.write(path, audio, SR, subtype="PCM_24")
    return path


def test_granular_output_is_continuous_and_records_junctions(tmp_path):
    source = texture_file(tmp_path / "t.flac", 60, 1)
    generator = GranularTexture.create(SR, np.random.SeedSequence(3), 240, sources=[str(source)], grain_min_s=5, grain_max_s=10)
    audio = np.concatenate([generator.render(SR * 10) for _ in range(12)])
    assert audio.shape == (SR * 120, 2) and np.isfinite(audio).all()
    assert len(generator.junctions) >= 10
    assert all(0 < j < len(audio) for j in generator.junctions)
    rms = np.sqrt(np.mean(np.square(audio.reshape(-1, SR, 2)), axis=(1, 2)))
    assert rms.min() > 0.3 * np.median(rms)  # equal-power crossfades: no dips at joins


def test_granular_long_render_from_short_source_passes_loop_and_click_gates(tmp_path):
    recipe = load_recipe("rain_window_real")
    source = texture_file(tmp_path / "window.flac", 90, 2)
    instance = instantiate(recipe, 5)
    instance["layers"][0].update(generator="granular", params={**recipe.layers[0].granular.model_dump(), "sources": [str(source)]})
    result = render_program(recipe, instance, 600, SR, -18.0, -1.0, tmp_path / "work", tmp_path / "out.flac")
    report = check_audio(result.path, AudioThresholds(expected_duration_s=600), junctions_s=result.junctions_s)
    assert len(result.junctions_s) > 20
    assert report.passed, [f"{c.name}={c.value}" for c in report.failures()]


def test_sample_event_is_pitched_and_faded(tmp_path):
    source = texture_file(tmp_path / "call.flac", 2, 4)
    event = SampleEvent(SR, np.random.default_rng(1), sources=[str(source)], pitch_cents=100).render()
    assert abs(len(event) - SR * 2) < SR * 0.12
    assert np.abs(event[0]).max() < 1e-3 and np.abs(event).max() == pytest.approx(1.0)


class FakeProvider:
    name = "fake"

    def __init__(self, tmp: Path, licenses: list[str], unavailable: bool = False):
        self.tmp, self.licenses, self.unavailable = tmp, licenses, unavailable
        self.fetched = []

    def search(self, query):
        if self.unavailable:
            raise ProviderUnavailableError("offline")
        return [
            SoundCandidate(
                "fake", str(i), f"creek {i}", 90, 48000, 2, "wav", ["water"], "Recorded with a Zoom H5 at the river bank.",
                "rec", f"https://example.org/{i}", lic, {"num_downloads": 100, "avg_rating": 4, "num_ratings": 3},
            )
            for i, lic in enumerate(self.licenses)
            if str(i) not in query.exclude_ids
        ]

    def license_info(self, candidate):
        lt = normalize_license(candidate.raw_license)
        return None if lt is None else LicenseRecord(lt, candidate.source_url, candidate.author, CANONICAL_URLS[lt], title=candidate.name)

    def fetch(self, candidate, cache_dir):
        raw = texture_file(self.tmp / f"raw{candidate.asset_id}.wav", 90, int(candidate.asset_id) + 10)
        target = cache_dir / f"{candidate.asset_id}.flac"
        normalize_to_flac(raw, target)
        self.fetched.append(candidate.asset_id)
        return FetchedSound(candidate, target, sha256_file(raw), self.license_info(candidate))

    def cost_estimate(self, query):
        return 0.0


@pytest.fixture
def db(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


VALIDATOR = LicenseValidator([LicenseType.CC0, LicenseType.PROCEDURAL, LicenseType.OWN])


def test_selector_uses_only_whitelisted_recordings_and_reports_them(db, tmp_path):
    recipe = load_recipe("rain_window_real")
    provider = FakeProvider(tmp_path, ["Creative Commons 0", "Attribution NonCommercial", "http://creativecommons.org/publicdomain/zero/1.0/"])
    instance = instantiate(recipe, 9)
    with session_scope(db) as session:
        resolution = SourceSelector(session, lambda name, lib: provider if name == "freesound" else None, VALIDATOR, tmp_path / "cache", 9).resolve(recipe, instance)
    layer = instance["layers"][0]
    assert layer["generator"] == "granular"
    assert provider.fetched == ["0", "2"]  # the NC sound is never downloaded
    report = build_license_report("run", resolution.resources, VALIDATOR)
    assert report.valid
    kinds = {entry["license_type"] for entry in report.data["resources"]}
    assert kinds == {"CC0", "PROCEDURAL"}
    assert all(e["provenance_notes"] for e in report.data["resources"] if e["license_type"] == "CC0")


def test_selector_reuses_catalog_without_calling_provider(db, tmp_path):
    recipe = load_recipe("rain_window_real")
    first = FakeProvider(tmp_path, ["Creative Commons 0"] * 6)
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: first, VALIDATOR, tmp_path / "cache", 1).resolve(recipe, instantiate(recipe, 1))
    offline = FakeProvider(tmp_path, [], unavailable=True)
    instance = instantiate(recipe, 2)
    with session_scope(db) as session:
        resolution = SourceSelector(session, lambda n, l: offline, VALIDATOR, tmp_path / "cache", 2).resolve(recipe, instance)
    assert instance["layers"][0]["generator"] == "granular"
    assert any("offline" in note for note in resolution.notes)


def test_selector_falls_back_to_procedural_and_blacklist_is_respected(db, tmp_path):
    recipe = load_recipe("rain_window_real")
    provider = FakeProvider(tmp_path, ["Creative Commons 0"])
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: provider, VALIDATOR, tmp_path / "cache", 1).resolve(recipe, instantiate(recipe, 1))
        sound = catalog.find(session, "rain_window_texture", "texture", VALIDATOR)[0]
        catalog.blacklist(session, sound.id, "Content ID claim")
    nothing = FakeProvider(tmp_path, [])
    instance = instantiate(recipe, 3)
    with session_scope(db) as session:
        resolution = SourceSelector(session, lambda n, l: nothing, VALIDATOR, tmp_path / "cache", 3).resolve(recipe, instance)
    assert instance["layers"][0]["generator"] == "rain_window"
    assert instance["layers"][0]["source"] == "procedural_fallback"
    assert any("procedural" in note for note in resolution.notes)


def test_required_event_without_sources_fails_but_optional_is_skipped(db, tmp_path):
    recipe = load_recipe("cloud_forest_birds")
    with session_scope(db) as session, pytest.raises(SourceUnavailableError, match="birds"):
        SourceSelector(session, lambda n, l: None, VALIDATOR, tmp_path / "cache", 4).resolve(recipe, instantiate(recipe, 4))

    data = recipe.model_dump()
    data["events"][0]["required"] = False
    optional = type(recipe).model_validate(data)
    instance = instantiate(optional, 4)
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: None, VALIDATOR, tmp_path / "cache", 4).resolve(optional, instance)
    assert instance["events"][0].get("skipped") is True
    assert instance["layers"][0]["generator"] == "wind"


def test_required_library_layer_without_fallback_raises(db, tmp_path):
    recipe = load_recipe("rain_window_real")
    data = recipe.model_dump()
    data["layers"][0]["generator"] = None
    broken = type(recipe).model_validate(data)
    with session_scope(db) as session, pytest.raises(SourceUnavailableError):
        SourceSelector(session, lambda n, l: None, VALIDATOR, tmp_path / "cache", 1).resolve(broken, instantiate(broken, 1))
