"""Titles: [What] + [Where, if it applies] + [Duration] + [What it is for] — literal, not creative."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from itertools import product

import numpy as np

from app.audio.recipe import Recipe
from app.metadata.language import is_english_only
from app.research.suggest import RankedTerm

MAX_TITLE = 100
IDEAL_RANGE = (45, 80)
SIMILARITY_LIMIT = 0.9

PURPOSES = {
    "sleep": ["for Deep Sleep", "for Sleeping", "for Sleep & Relaxation", "to Fall Asleep Fast"],
    "study": ["for Study & Focus", "for Studying", "for Focus & Concentration", "for Work & Study"],
    "relaxation": ["for Relaxation & Meditation", "for Relaxation", "for Stress Relief", "for Meditation"],
}
MIXED_PURPOSE = "for Sleep, Study & Relaxation"
SEPARATORS = [" | ", " — ", " • "]
# Place words that already locate the ambience; "Costa Rica" is inserted before them instead of added again.
PLACE_WORDS = ("Cloud Forest", "Rainforest", "Jungle", "Forest", "Beach")
JUNGLE_TAGS = {"jungle", "rainforest", "forest", "cloud_forest"}


def place_variants(recipe: Recipe, what: str) -> list[str]:
    """Title heads with and without Costa Rica, only where the visual justifies it."""
    heads = [what]
    if not recipe.costa_rica_eligible:
        return heads
    for word in PLACE_WORDS:
        if re.search(rf"\bthe {word}\b", what, re.IGNORECASE):
            heads.append(re.sub(rf"\bthe ({word})\b", r"the Costa Rica \1", what, count=1, flags=re.IGNORECASE))
            return heads
        if re.search(rf"\b{word}\b", what, re.IGNORECASE):
            heads.append(f"Costa Rica {what}")
            return heads
    heads += [f"Costa Rica {what}", f"{what} in Costa Rica"]
    if JUNGLE_TAGS & set(recipe.visual_tags):
        heads.append(f"Costa Rica Rainforest {what}")
    return heads


@dataclass
class ScoredTitle:
    text: str
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


def duration_label(minutes: float) -> str:
    hours = minutes / 60
    if abs(hours - round(hours)) < 0.05:
        n = int(round(hours))
        return f"{n} Hour" if n == 1 else f"{n} Hours"
    return f"{hours:.1f}".rstrip("0").rstrip(".") + " Hours"


def _title_case(phrase: str) -> str:
    small = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with", "&"}
    words = phrase.split()
    return " ".join(w if w.isupper() and len(w) > 1 else (w.lower() if i and w.lower() in small else w[:1].upper() + w[1:]) for i, w in enumerate(words))


def what_phrases(recipe: Recipe, research: list[RankedTerm]) -> list[str]:
    phrases = [recipe.name]
    for term in research[:12]:
        cleaned = re.sub(r"\b(for|to)\b.*$", "", term.term).strip()
        cleaned = re.sub(r"\b(\d+\s*hours?|hours?|sleeping|sleep|study|studying|relaxing|relaxation|focus)\b", "", cleaned).strip()
        if 2 <= len(cleaned.split()) <= 5:
            phrases.append(_title_case(cleaned))
    unique = []
    for p in phrases:
        if p.lower() not in {u.lower() for u in unique}:
            unique.append(p)
    return unique[:6]


def candidates(recipe: Recipe, minutes: float, research: list[RankedTerm], rng: np.random.Generator, limit: int = 40) -> list[str]:
    duration = duration_label(minutes)
    purposes = [p for s in recipe.subniches for p in PURPOSES[s][:2]]
    if len(recipe.subniches) >= 3:
        purposes.append(MIXED_PURPOSE)
    whats = what_phrases(recipe, research)
    titles = set()
    for what in whats:
        for head, purpose, sep in product(place_variants(recipe, what), purposes, SEPARATORS):
            titles.add(f"{head} {purpose}{sep}{duration}")
            titles.add(f"{head}{sep}{duration} {purpose}")
    pool = sorted(titles)
    if len(pool) > limit:
        pool = [pool[i] for i in rng.choice(len(pool), limit, replace=False)]
    return pool


def score_title(title: str, recipe: Recipe, research: list[RankedTerm], recent_titles: list[str]) -> ScoredTitle:
    lower = title.lower()
    breakdown: dict[str, float] = {}
    if len(title) > MAX_TITLE or not is_english_only(title):
        return ScoredTitle(title, -1.0, {"rejected": 1.0})

    breakdown["clarity"] = (1.0 if re.search(r"\d+(\.\d)? hours?", lower) else 0) + (1.0 if re.search(r"\bfor\b|\bto\b", lower) else 0)
    top = research[:10]
    total = sum(t.score for t in top) or 1.0
    breakdown["keywords"] = 3.0 * sum(t.score for t in top if t.term in lower) / total
    word_hits = {w for t in top for w in t.term.split() if len(w) > 3 and w in lower}
    breakdown["keyword_words"] = min(len(word_hits), 5) * 0.2

    low, high = IDEAL_RANGE
    breakdown["length"] = 1.0 if low <= len(title) <= high else max(0.0, 1 - abs(len(title) - (low if len(title) < low else high)) / 30)
    words = re.findall(r"[a-z]+", lower)
    repeats = sum(c - 1 for c in (words.count(w) for w in set(words)) if c > 1 and len(words) > 0)
    breakdown["stuffing"] = -0.5 * max(0, repeats - 1) - 0.5 * max(0, sum(title.count(s.strip()) for s in SEPARATORS) - 1)
    if recipe.costa_rica_eligible and "costa rica" in lower:
        breakdown["differentiator"] = 0.6

    similarity = max((difflib.SequenceMatcher(None, lower, r.lower()).ratio() for r in recent_titles), default=0.0)
    breakdown["novelty"] = -5.0 if similarity >= SIMILARITY_LIMIT else -2.0 * max(0.0, similarity - 0.7)
    return ScoredTitle(title, round(sum(breakdown.values()), 3), {k: round(v, 3) for k, v in breakdown.items()})


def choose_title(recipe: Recipe, minutes: float, research: list[RankedTerm], recent_titles: list[str], seed: int) -> tuple[ScoredTitle, list[ScoredTitle]]:
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x717]))
    scored = [score_title(t, recipe, research, recent_titles) for t in candidates(recipe, minutes, research, rng)]
    valid = sorted((s for s in scored if s.score >= 0 and s.breakdown.get("novelty", 0) > -5), key=lambda s: (-s.score, s.text))
    if not valid:
        raise ValueError("no valid title candidate (all too long, non-English or too similar to recent titles)")
    # Pick among the best few so consecutive videos of the same recipe do not share a title.
    top = valid[: min(3, len(valid))]
    chosen = top[int(rng.integers(len(top)))]
    return chosen, valid
