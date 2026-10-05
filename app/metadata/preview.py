from __future__ import annotations

import re

from app.audio.recipe import Recipe
from app.metadata.builder import MetadataPackage, build_metadata
from app.research.suggest import RankedTerm, SuggestClient, rank_terms
from app.utils.config import Settings

BLOCKED_WORDS = {"asmr", "music", "song", "songs", "lyrics", "remix", "piano", "guitar", "meme", "prank", "live", "shorts", "minecraft", "fortnite"}


def relevant_words(recipe: Recipe) -> set[str]:
    words = set()
    for text in [recipe.name, *recipe.keywords, *recipe.visual_tags]:
        words |= {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}
    return words - {"for", "the", "and", "with", "sounds", "sound"}


def research_recipe(recipe: Recipe, settings: Settings) -> list[RankedTerm]:
    client = SuggestClient(settings.cache_dir)
    return rank_terms(recipe.keywords, relevant_words(recipe), client, BLOCKED_WORDS)


def preview_metadata(recipe: Recipe, minutes: float, settings: Settings, seed: int, use_research: bool, recent_titles: list[str] | None = None) -> MetadataPackage:
    research = research_recipe(recipe, settings) if use_research else []
    return build_metadata(recipe, minutes, research, recent_titles or [], seed, settings.youtube_category_id, filmed_in_costa_rica=True,
                          visual_style=settings.visual_style.value)
