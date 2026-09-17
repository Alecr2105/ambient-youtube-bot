from __future__ import annotations

import pytest

from app.audio.recipe import all_recipes, load_recipe
from app.database.migrate import upgrade_to_head
from app.database.models import Visual
from app.database.session import make_engine, session_scope
from app.visuals.coverage import COMFORTABLE_SECONDS, recipe_coverage, render_report, tag_demand


@pytest.fixture
def engine(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


def clip(path: str, tags: list[str], duration: float = 900.0, kind: str = "video") -> Visual:
    return Visual(path=path, type=kind, duration=duration, width=3840, height=2160, fps=30, tags=tags, checksum=path)


def test_empty_library_reports_every_recipe_as_missing(engine):
    with session_scope(engine) as session:
        report = recipe_coverage(session)
        assert report and all(not coverage.ready for coverage in report)
        assert all(coverage.missing_tags == tuple(sorted(t.lower() for t in coverage.recipe.visual_tags)) for coverage in report)
        # The tags shared by the most recipes come first, so the owner films those.
        demand = tag_demand(report)
        assert demand[0][1] >= demand[-1][1]
        assert {"rain", "forest", "jungle"} <= {tag for tag, _ in demand}


def test_ready_thin_and_missing_are_distinguished(engine):
    rain = load_recipe("heavy_rain_window")  # visual_tags: rain, window
    fire = load_recipe("fireplace")  # visual_tags: fire, fireplace, cabin
    with session_scope(engine) as session:
        session.add_all([
            clip("/rain_a.mp4", ["rain", "window"]),
            clip("/rain_b.mp4", ["rain"]),
            clip("/fire.mp4", ["fire"], duration=120.0),
        ])
    with session_scope(engine) as session:
        by_slug = {c.recipe.slug: c for c in recipe_coverage(session, [rain, fire, load_recipe("ocean_waves")])}

    assert by_slug["heavy_rain_window"].ready and not by_slug["heavy_rain_window"].thin
    assert by_slug["heavy_rain_window"].video_clips == 2
    assert by_slug["heavy_rain_window"].video_seconds >= COMFORTABLE_SECONDS

    thin = by_slug["fireplace"]
    assert thin.ready and thin.thin  # one short clip is enough to produce, not enough for variety
    assert thin.matched_tags == ("fire",) and thin.missing_tags == ("cabin", "fireplace")

    assert not by_slug["ocean_waves"].ready


def test_report_names_what_to_film_and_hides_covered_recipes_from_the_demand(engine):
    with session_scope(engine) as session:
        session.add_all([clip("/rain.mp4", ["rain", "window"])])
    with session_scope(engine) as session:
        report = recipe_coverage(session)
        text = render_report(report, "assets/visuals")
        demand = dict(tag_demand(report))

    assert "[   THIN   ] heavy_rain_window" in text  # produces, but one clip is little variety
    assert "[NO FOOTAGE] ocean_waves" in text
    assert "index-visuals" in text
    # 'window' is only needed by recipes that rain.mp4 already covers, so nothing waits on it.
    assert "window" not in demand and demand["ocean"] == 1


def test_disabled_recipes_are_left_out(engine):
    with session_scope(engine) as session:
        slugs = {coverage.recipe.slug for coverage in recipe_coverage(session)}
    assert slugs == {recipe.slug for recipe in all_recipes() if recipe.enabled}
