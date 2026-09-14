"""Daily choice of what to produce: variety, subniche weights, footage availability, season."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.recipe import Recipe, all_recipes, recipe_checksum
from app.database.models import Ambient, PublishHistory, Recipe as RecipeRow, Video
from app.database.states import VideoState
from app.quality.duplicates import recent_concept_repeats
from app.utils.config import Settings
from app.visuals.matcher import NoMatchingVisualsError, choose_visuals

NO_REPEAT_DAYS = 4
WINTER_MONTHS = {11, 12, 1, 2}
COZY_WORDS = {"fire", "fireplace", "cabin", "cozy"}


@dataclass(frozen=True)
class Plan:
    recipe: Recipe
    publish_date: date
    publish_at: datetime
    seed: int
    duration_seconds: int
    score: float


def recent_slugs(session: Session, before: date, days: int) -> set[str]:
    since = before - timedelta(days=days)
    rows = session.execute(
        select(RecipeRow.slug).join(Video, Video.recipe_id == RecipeRow.id)
        .where(Video.target_publish_date >= since, Video.target_publish_date < before + timedelta(days=days), Video.state != VideoState.FAILED)
    ).scalars().all()
    published = session.execute(select(PublishHistory.ambient_slug).where(PublishHistory.published_on >= since)).scalars().all()
    return set(rows) | set(published)


def publish_time(settings: Settings, day: date, rng: np.random.Generator) -> datetime:
    zone = ZoneInfo(settings.publish_timezone)
    minute = int(rng.integers(0, settings.publish_window_minutes + 1)) if settings.publish_window_minutes else 0
    return datetime.combine(day, time(settings.publish_hour), tzinfo=zone) + timedelta(minutes=minute)


PERFORMANCE_WINDOW_DAYS = 90
MIN_VIDEOS_FOR_PERFORMANCE = 3


def performance_factors(session: Session, day: date) -> dict[str, float]:
    """Recipes whose videos get more views than the channel average are favoured, gently:
    the factor is bounded so a weaker ambient still gets produced and can recover."""
    since = day - timedelta(days=PERFORMANCE_WINDOW_DAYS)
    rows = session.execute(
        select(PublishHistory.ambient_slug, PublishHistory.views).where(PublishHistory.published_on >= since, PublishHistory.views.is_not(None))
    ).all()
    if not rows:
        return {}
    # Medians, so one viral video neither inflates the channel baseline nor its own recipe.
    channel_median = float(np.median([v for _, v in rows]))
    by_recipe: dict[str, list[int]] = {}
    for slug, views in rows:
        by_recipe.setdefault(slug, []).append(views)
    factors = {}
    for slug, views in by_recipe.items():
        if len(views) >= MIN_VIDEOS_FOR_PERFORMANCE and channel_median > 0:
            factors[slug] = float(np.clip((float(np.median(views)) / channel_median) ** 0.3, 0.8, 1.3))
    return factors


def score_recipe(recipe: Recipe, settings: Settings, day: date, rng: np.random.Generator, performance: dict[str, float] | None = None) -> float:
    weight = max(settings.subniche_weights.get(s, 0.0) for s in recipe.subniches)
    score = weight * float(rng.uniform(0.6, 1.4))
    words = {w.lower() for w in recipe.visual_tags + recipe.slug.split("_")}
    if day.month in WINTER_MONTHS and words & COZY_WORDS:
        score *= 1.4
    if recipe.costa_rica_eligible:
        score *= 1.1  # the channel's differentiator
    return score * (performance or {}).get(recipe.slug, 1.0)


def choose_plan(session: Session, settings: Settings, day: date, seed: int | None = None, exclude: set[str] = frozenset()) -> Plan:
    seed = seed if seed is not None else secrets.randbits(32)
    rng = np.random.default_rng(np.random.SeedSequence([seed, day.toordinal()]))
    blocked = recent_slugs(session, day, NO_REPEAT_DAYS) | set(exclude)
    performance = performance_factors(session, day)
    scored = []
    for recipe in all_recipes():
        if not recipe.enabled or recipe.slug in blocked:
            continue
        try:
            visuals = choose_visuals(session, recipe.visual_tags)
        except NoMatchingVisualsError:
            continue
        if recent_concept_repeats(session, recipe.slug, [v.id for v in visuals], day):
            continue
        scored.append((score_recipe(recipe, settings, day, rng, performance), recipe))
    if not scored:
        raise NoMatchingVisualsError("no enabled recipe has matching footage and is outside the no-repeat window")
    score, recipe = max(scored, key=lambda item: item[0])
    if settings.video_duration is not None:
        minutes = settings.video_duration
    else:
        minutes = int(rng.integers(settings.video_duration_min // 30, settings.video_duration_max // 30 + 1)) * 30
        minutes = min(max(minutes, settings.video_duration_min), settings.video_duration_max)
    return Plan(recipe, day, publish_time(settings, day, rng), seed, int(minutes * 60), round(score, 3))


def recipe_row(session: Session, recipe: Recipe, path: str) -> RecipeRow:
    checksum = recipe_checksum(recipe)
    row = session.scalar(select(RecipeRow).where(RecipeRow.slug == recipe.slug, RecipeRow.checksum == checksum))
    if row is not None:
        return row
    ambient = session.scalar(select(Ambient).where(Ambient.slug == recipe.slug))
    if ambient is None:
        ambient = Ambient(slug=recipe.slug, name=recipe.name, subniche=recipe.subniches[0], enabled=recipe.enabled)
        session.add(ambient)
        session.flush()
    row = RecipeRow(ambient_id=ambient.id, slug=recipe.slug, path=path, checksum=checksum, content=recipe.model_dump(mode="json"))
    session.add(row)
    session.flush()
    return row
