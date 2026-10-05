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
from app.audio.recipe import Recipe, instantiate, load_recipe
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


def one_layer(slug: str) -> Recipe:
    """The recipe's lead texture only, so each test looks at one library item."""
    data = load_recipe(slug).model_dump()
    data["layers"], data["events"] = data["layers"][:1], []
    return Recipe.model_validate(data)


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
    recipe = one_layer("rain_window_real")
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


@pytest.fixture
def db(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


VALIDATOR = LicenseValidator([LicenseType.CC0, LicenseType.OWN])


def test_selector_uses_only_whitelisted_recordings_and_reports_them(db, tmp_path):
    recipe = one_layer("rain_window_real")
    provider = FakeProvider(tmp_path, ["Creative Commons 0", "Attribution NonCommercial", "http://creativecommons.org/publicdomain/zero/1.0/"])
    instance = instantiate(recipe, 9)
    with session_scope(db) as session:
        resolution = SourceSelector(session, lambda name, lib: provider if name == "freesound" else None, VALIDATOR, tmp_path / "cache", 9).resolve(recipe, instance)
    layer = instance["layers"][0]
    assert layer["generator"] == "granular"
    assert provider.fetched == ["0", "2"]  # the NC sound is never downloaded
    report = build_license_report("run", resolution.resources, VALIDATOR)
    assert report.valid
    assert {entry["license_type"] for entry in report.data["resources"]} == {"CC0"}  # nothing synthetic
    assert all(e["provenance_notes"] for e in report.data["resources"])


def test_selector_reuses_catalog_without_calling_provider(db, tmp_path):
    recipe = one_layer("rain_window_real")
    first = FakeProvider(tmp_path, ["Creative Commons 0"] * 6)
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: first, VALIDATOR, tmp_path / "cache", 1).resolve(recipe, instantiate(recipe, 1))
    offline = FakeProvider(tmp_path, [], unavailable=True)
    instance = instantiate(recipe, 2)
    with session_scope(db) as session:
        resolution = SourceSelector(session, lambda n, l: offline, VALIDATOR, tmp_path / "cache", 2).resolve(recipe, instance)
    assert instance["layers"][0]["generator"] == "granular"
    assert any("offline" in note for note in resolution.notes)


def test_blacklisted_recording_is_never_used_and_nothing_synthetic_takes_its_place(db, tmp_path):
    recipe = one_layer("rain_window_real")
    provider = FakeProvider(tmp_path, ["Creative Commons 0"])
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: provider, VALIDATOR, tmp_path / "cache", 1).resolve(recipe, instantiate(recipe, 1))
        sound = catalog.find(session, "rain_window_texture", "texture", VALIDATOR)[0]
        catalog.blacklist(session, sound.id, "Content ID claim")
    nothing = FakeProvider(tmp_path, [])
    with session_scope(db) as session, pytest.raises(SourceUnavailableError, match="window"):
        SourceSelector(session, lambda n, l: nothing, VALIDATOR, tmp_path / "cache", 3).resolve(recipe, instantiate(recipe, 3))


def test_required_event_without_sources_fails_but_optional_is_skipped(db, tmp_path):
    data = load_recipe("thunderstorm").model_dump()
    data["layers"] = data["layers"][:1]
    textures = FakeProvider(tmp_path, ["Creative Commons 0"] * 2)

    def only_textures(name, library):
        return textures if library.category.endswith("_texture") else None

    required = Recipe.model_validate(data)
    with session_scope(db) as session, pytest.raises(SourceUnavailableError, match="thunder"):
        SourceSelector(session, only_textures, VALIDATOR, tmp_path / "cache", 4).resolve(required, instantiate(required, 4))

    data["events"][0]["required"] = False
    optional = Recipe.model_validate(data)
    instance = instantiate(optional, 4)
    with session_scope(db) as session:
        SourceSelector(session, only_textures, VALIDATOR, tmp_path / "cache", 4).resolve(optional, instance)
    assert instance["events"][0].get("skipped") is True
    assert instance["layers"][0]["generator"] == "granular"


def with_burst(path: Path, seconds: float, seed: int, burst_db: float) -> Path:
    """A steady take with three loud seconds in the middle, like rain with one thunder clap in it."""
    texture_file(path, seconds, seed)
    audio, _ = sf.read(path, always_2d=True)
    middle = len(audio) // 2
    audio[middle : middle + SR * 3] *= 10 ** (burst_db / 20)
    sf.write(path, audio / max(1.0, np.abs(audio).max() / 0.9), SR, subtype="PCM_24")
    return path


def test_loudness_profile_reports_usual_level_and_the_loudest_moment(tmp_path):
    from app.audio.processor.loudness import loudness_profile

    steady, _ = sf.read(texture_file(tmp_path / "steady.wav", 60, 1), always_2d=True)
    stormy, _ = sf.read(with_burst(tmp_path / "stormy.wav", 60, 1, burst_db=14), always_2d=True)
    steady_level, steady_spread = loudness_profile(steady, SR)
    _stormy_level, stormy_spread = loudness_profile(stormy, SR)
    assert steady_spread < 3 < 10 < stormy_spread
    quieter_level, _ = loudness_profile(steady * 0.1, SR)
    assert quieter_level == pytest.approx(steady_level - 20, abs=0.1)


class BurstProvider(FakeProvider):
    """Candidate 0 is a steady take; candidate 1 has a thunder clap in the middle."""

    def fetch(self, candidate, cache_dir):
        raw = self.tmp / f"raw{candidate.asset_id}.wav"
        if candidate.asset_id == "1":
            with_burst(raw, 90, 11, burst_db=14)
        else:
            texture_file(raw, 90, 10)
        target = cache_dir / f"{candidate.asset_id}.flac"
        normalize_to_flac(raw, target)
        self.fetched.append(candidate.asset_id)
        return FetchedSound(candidate, target, sha256_file(raw), self.license_info(candidate))


def test_uneven_texture_is_never_catalogued(db, tmp_path):
    recipe = one_layer("rain_window_real")
    provider = BurstProvider(tmp_path, ["Creative Commons 0"] * 2)
    instance = instantiate(recipe, 6)
    with session_scope(db) as session:
        SourceSelector(session, lambda n, l: provider if n == "freesound" else None, VALIDATOR,
                       tmp_path / "cache", 6).resolve(recipe, instance)
        kept = catalog.find(session, "rain_window_texture", "texture", VALIDATOR)
    assert provider.fetched == ["0", "1"]  # both measured...
    assert [s.asset_id for s in kept] == ["0"]  # ...only the steady one is used


def test_granular_matches_recordings_by_level_so_switching_takes_is_not_a_swell(tmp_path):
    from app.audio.processor.loudness import loudness_profile

    loud = texture_file(tmp_path / "loud.wav", 60, 3)
    quiet_audio, _ = sf.read(texture_file(tmp_path / "quiet_src.wav", 60, 4), always_2d=True)
    quiet = tmp_path / "quiet.wav"
    sf.write(quiet, quiet_audio * 10 ** (-18 / 20), SR, subtype="PCM_24")  # an 18 dB quieter take
    generator = GranularTexture.create(SR, np.random.SeedSequence(8), 300, sources=[str(loud), str(quiet)],
                                       grain_min_s=8, grain_max_s=12)
    audio = np.concatenate([generator.render(SR * 10) for _ in range(18)])
    _level, spread = loudness_profile(audio, SR)
    assert spread < 4  # with RMS-free, level-matched grains there are no 18 dB swells between takes
