from __future__ import annotations

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


def test_recipe_rejects_unknown_generator():
    data = load_recipe("fireplace").model_dump()
    data["layers"][0]["generator"] = "dragon"
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


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render")
    recipe = load_recipe("thunderstorm")
    results = {}
    for seed in (101, 202):
        results[seed] = render_program(
            recipe, instantiate(recipe, seed), 90, SR, -18.0, -1.0, tmp / f"work{seed}", tmp / f"{seed}.flac", block_seconds=5
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
