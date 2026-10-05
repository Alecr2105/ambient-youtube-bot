from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from app.audio.generators.base import SmoothRandom
from app.audio.mixer.events import schedule_events
from app.audio.mixer.render import render_program
from app.audio.recipe import Recipe, all_recipes, instantiate, load_recipe
from app.quality.audio import AudioThresholds, check_audio

SR = 48000


def test_all_recipes_are_valid():
    recipes = all_recipes()
    assert len(recipes) >= 10
    assert len({r.slug for r in recipes}) == len(recipes)


@pytest.mark.parametrize(
    "change",
    [
        lambda layer: layer.update(generator="rain"),  # synthetic sound is no longer accepted
        lambda layer: layer.pop("library"),  # every layer must come from real recordings
    ],
)
def test_recipe_accepts_only_recorded_sound(change):
    data = load_recipe("fireplace").model_dump()
    change(data["layers"][0])
    with pytest.raises(ValidationError):
        Recipe.model_validate(data)


def test_instances_differ_by_seed_and_repeat_by_seed():
    recipe = load_recipe("heavy_rain_window")
    assert instantiate(recipe, 1) == instantiate(recipe, 1)
    assert instantiate(recipe, 1)["layers"] != instantiate(recipe, 2)["layers"]


def test_events_are_irregular_and_respect_gap():
    recipe = load_recipe("thunderstorm")
    instance = instantiate(recipe, 11)
    duration = 4 * 3600
    intensity = SmoothRandom(np.random.default_rng(0), SR, duration, 600, 1800)
    occurrences = schedule_events(recipe, instance, duration, SR, intensity, np.random.SeedSequence(5))
    onsets = np.array([o.onset for o in occurrences]) / SR
    gaps = np.diff(onsets)
    assert len(onsets) > 20
    assert gaps.min() >= recipe.events[0].min_gap_s
    # Exponential-ish inter-arrival times: large spread relative to the mean, never a fixed grid.
    assert gaps.std() / gaps.mean() > 0.4


def recording(path: Path, seconds: float, seed: int, decay: bool = False) -> str:
    """A stand-in for a downloaded recording: band-limited noise (or a decaying burst for events)."""
    rng = np.random.default_rng(seed)
    audio = rng.standard_normal((int(SR * seconds), 2))
    audio = np.cumsum(audio, axis=0) * 0.02
    audio -= np.convolve(audio[:, 0], np.ones(480) / 480, mode="same")[:, None]
    if decay:
        audio *= np.exp(-np.linspace(0, 6, len(audio)))[:, None]
    audio *= 0.3 / np.abs(audio).max()
    sf.write(path, audio.astype(np.float32), SR, subtype="PCM_24")
    return str(path)


def resolve_with_recordings(recipe: Recipe, instance: dict, folder: Path) -> dict:
    """What the source selector does once it has picked catalog recordings."""
    textures = [recording(folder / f"texture{i}.wav", 70, seed=i) for i in range(2)]
    events = [recording(folder / f"event{i}.wav", 4, seed=10 + i, decay=True) for i in range(2)]
    for spec, layer in zip(recipe.layers, instance["layers"], strict=True):
        layer["generator"] = "granular"
        layer["params"] = {**spec.granular.model_dump(), "sources": textures}
    for spec, event in zip(recipe.events, instance["events"], strict=True):
        event["generator"] = "sample"
        event["params"] = {"sources": events, "pitch_cents": spec.pitch_cents}
    return instance


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render")
    recipe = load_recipe("thunderstorm")
    results = {}
    for seed in (101, 202):
        instance = resolve_with_recordings(recipe, instantiate(recipe, seed), tmp)
        results[seed] = render_program(
            recipe, instance, 90, SR, -18.0, -1.0, tmp / f"work{seed}", tmp / f"{seed}.flac", block_seconds=5
        )
    return results


def test_render_meets_loudness_and_peak_targets(rendered):
    result = rendered[101]
    report = check_audio(result.path, AudioThresholds(expected_duration_s=90, target_lufs=-18.0, max_true_peak_dbtp=-1.0))
    assert report.passed, [f"{c.name}={c.value}" for c in report.failures()]
    assert not (result.path.parent / "work101" / "premaster.rf64").exists()


def test_two_renders_of_same_recipe_differ(rendered):
    a, _ = sf.read(rendered[101].path)
    b, _ = sf.read(rendered[202].path)
    n = min(len(a), len(b))
    assert abs(np.corrcoef(a[:n, 0], b[:n, 0])[0, 1]) < 0.2
