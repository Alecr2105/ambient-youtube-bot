from __future__ import annotations

import json

import numpy as np
import pytest

from app.audio.recipe import all_recipes, load_recipe
from app.metadata.builder import build_metadata, build_tags
from app.metadata.language import is_english_only, spanish_findings
from app.metadata.titles import candidates, choose_title, duration_label, score_title
from app.research.suggest import RankedTerm, SuggestClient, rank_terms
from app.youtube.uploader import _tags_length, build_video_body

RESEARCH = [
    RankedTerm("heavy rain on window", 3.0, ("seed",)),
    RankedTerm("rain sounds for sleeping", 2.5, ("seed",)),
    RankedTerm("heavy rain sounds", 2.0, ("seed",)),
    RankedTerm("rain on window for sleep", 1.2, ("rain on window",)),
]


@pytest.mark.parametrize(("text", "ok"), [
    ("Costa Rica Rainforest Rain Sounds | 3 Hours for Sleep", True),
    ("Pura Vida Jungle Ambience at La Fortuna", True),
    ("Sonidos de lluvia para dormir", False),
    ("Rain Sounds — 4 Horas", False),
    ("Relajación total", False),
    ("Sin City rain", True),
])
def test_spanish_detection(text, ok):
    assert is_english_only(text) is ok, spanish_findings(text)


@pytest.mark.parametrize(("minutes", "label"), [(180, "3 Hours"), (240, "4 Hours"), (210, "3.5 Hours"), (60, "1 Hour")])
def test_duration_label(minutes, label):
    assert duration_label(minutes) == label


def test_candidates_follow_formula_and_limits():
    recipe = load_recipe("heavy_rain_window")
    pool = candidates(recipe, 240, RESEARCH, np.random.default_rng(1))
    assert pool
    for title in pool:
        assert "4 Hours" in title
        assert " for " in title or " to " in title
        assert "costa rica" not in title.lower()  # recipe is not Costa Rica eligible


def test_costa_rica_only_when_eligible():
    rain = load_recipe("gentle_rain")
    fire = load_recipe("fireplace")
    assert any("Costa Rica" in t for t in candidates(rain, 180, [], np.random.default_rng(2), limit=500))
    assert not any("Costa Rica" in t for t in candidates(fire, 180, [], np.random.default_rng(2), limit=500))


def test_scoring_rejects_long_spanish_and_near_duplicates():
    recipe = load_recipe("heavy_rain_window")
    assert score_title("x" * 101, recipe, RESEARCH, []).score < 0
    assert score_title("Lluvia para dormir — 4 Hours", recipe, RESEARCH, []).score < 0
    title = "Heavy Rain on Window for Deep Sleep — 4 Hours"
    fresh = score_title(title, recipe, RESEARCH, [])
    repeated = score_title(title, recipe, RESEARCH, ["Heavy Rain on Window for Deep Sleep — 4 Hours"])
    assert fresh.score > repeated.score + 4


def test_keyword_match_improves_score():
    recipe = load_recipe("heavy_rain_window")
    matched = score_title("Heavy Rain on Window for Deep Sleep — 4 Hours", recipe, RESEARCH, [])
    generic = score_title("Calm Water Ambience for Deep Sleep — 4 Hours", recipe, RESEARCH, [])
    assert matched.score > generic.score


def test_consecutive_videos_get_different_titles():
    recipe = load_recipe("heavy_rain_window")
    first, _ = choose_title(recipe, 240, RESEARCH, [], seed=1)
    second, _ = choose_title(recipe, 240, RESEARCH, [first.text], seed=2)
    assert first.text != second.text


def test_tags_fit_youtube_limit_and_skip_spanish():
    recipe = load_recipe("thunderstorm")
    research = [RankedTerm(f"thunderstorm sounds variant {i} for sleeping deeply", 1.0, ()) for i in range(40)]
    research.append(RankedTerm("sonidos de tormenta", 5.0, ()))
    tags = build_tags(recipe, research, filmed_in_costa_rica=True)
    assert _tags_length(tags) <= 500
    assert "sonidos de tormenta" not in tags
    assert "costa rica" not in tags or recipe.costa_rica_eligible


@pytest.mark.parametrize("recipe", all_recipes(), ids=lambda r: r.slug)
def test_every_recipe_produces_valid_english_upload_body(recipe):
    package = build_metadata(recipe, 210, [], [], seed=3, category_id="10", filmed_in_costa_rica=True)
    body = build_video_body(package.to_video_metadata(False))
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert package.spanish_problems() == {}
    assert "http" not in package.description
    assert len(package.title) <= 100


def test_tags_never_claim_a_different_duration():
    recipe = load_recipe("heavy_rain_window")
    research = [RankedTerm(t, 1.0, ()) for t in ("rain sounds 24 hours", "rain sounds 1 hour", "rain sounds 4 hours", "rain sounds for sleeping")]
    tags = build_tags(recipe, research, False, minutes=240)
    assert "rain sounds 4 hours" in tags and "rain sounds for sleeping" in tags
    assert "rain sounds 24 hours" not in tags and "rain sounds 1 hour" not in tags


def test_spanish_localization_is_opt_in_and_never_touches_main_fields():
    recipe = load_recipe("gentle_rain").model_copy(update={"name_es": "Lluvia suave"})
    off = build_metadata(recipe, 180, [], [], seed=1, category_id="10", filmed_in_costa_rica=True)
    on = build_metadata(recipe, 180, [], [], seed=1, category_id="10", filmed_in_costa_rica=True, enable_es_localization=True)
    assert off.localizations == {}
    assert on.localizations["es"]["title"].startswith("Lluvia suave para")
    assert on.title == off.title and on.spanish_problems() == {}
    body = build_video_body(on.to_video_metadata(False))
    assert body["localizations"]["es"]["title"] == on.localizations["es"]["title"]


def test_attribution_is_appended_when_given():
    recipe = load_recipe("river_stream")
    package = build_metadata(recipe, 180, [], [], seed=1, category_id="10", filmed_in_costa_rica=True, attribution="Sound credits (from freesound.org):\n\"Creek\" by rec")
    assert "Sound credits" in package.description


class FakeFetch:
    def __init__(self, table, fail=False):
        self.table, self.fail, self.calls = table, fail, 0

    def __call__(self, url):
        self.calls += 1
        if self.fail:
            raise OSError("blocked")
        query = url.split("q=")[1].replace("+", " ").split("&")[0]
        return json.dumps([query, self.table.get(query, [])]).encode()


def test_rank_terms_prefers_early_and_repeated_suggestions_and_filters_noise(tmp_path):
    fetch = FakeFetch({
        "rain sounds": ["rain sounds for sleeping", "rain sounds 10 hours", "rain sounds asmr", "lluvia para dormir"],
        "heavy rain": ["heavy rain sounds", "rain sounds for sleeping", "heavy metal music"],
    })
    client = SuggestClient(tmp_path, fetch, sleep=lambda s: None)
    ranked = rank_terms(["rain sounds", "heavy rain"], {"rain", "sleeping", "sounds"}, client, blocked_words={"asmr"})
    terms = [r.term for r in ranked]
    assert terms.index("rain sounds for sleeping") < terms.index("rain sounds 10 hours")
    assert "lluvia para dormir" not in terms and "heavy metal music" not in terms and "rain sounds asmr" not in terms
    calls = fetch.calls
    rank_terms(["rain sounds"], {"rain"}, client)
    assert fetch.calls == calls  # served from cache


def test_misleading_and_off_topic_suggestions_are_dropped(tmp_path):
    fetch = FakeFetch({"thunderstorm sounds": [
        "thunderstorm sounds for sleeping", "thunderstorm sounds black screen", "rain sounds no ads",
        "tropical storm melissa", "thunderstorm sounds bass", "thunderstorm sounds 10 hours", "thunderstorm sounds 2024",
    ]})
    client = SuggestClient(tmp_path, fetch, sleep=lambda s: None)
    terms = [r.term for r in rank_terms(["thunderstorm sounds"], {"thunderstorm", "rain", "storm"}, client)]
    assert "thunderstorm sounds for sleeping" in terms and "thunderstorm sounds 10 hours" in terms
    for bad in ("black screen", "no ads", "melissa", "bass", "2024"):
        assert not any(bad in t for t in terms), bad


def test_suggest_failures_degrade_to_seeds(tmp_path):
    client = SuggestClient(tmp_path, FakeFetch({}, fail=True), sleep=lambda s: None)
    ranked = rank_terms(["rain sounds"], {"rain"}, client)
    assert [r.term for r in ranked] == ["rain sounds"]
