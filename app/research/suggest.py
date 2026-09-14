"""Keyword research from YouTube's public search suggestions.

The suggest endpoint is not an official, documented API: it can change or throttle at any
time. Results are cached, requests are spaced out, and any failure degrades to the recipe's
seed keywords instead of stopping the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.metadata.language import is_english_only

log = logging.getLogger(__name__)

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
CACHE_TTL_S = 7 * 86400
MIN_INTERVAL_S = 1.0

Fetch = Callable[[str], bytes]


def default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ambient-bot keyword research)"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


@dataclass(frozen=True)
class RankedTerm:
    term: str
    score: float
    sources: tuple[str, ...]


class SuggestClient:
    def __init__(self, cache_dir: Path, fetch: Fetch = default_fetch, sleep: Callable[[float], None] = time.sleep):
        self.cache_dir = cache_dir / "research" / "suggest"
        self.fetch = fetch
        self.sleep = sleep
        self._last_request = 0.0

    def suggestions(self, query: str) -> list[str]:
        key = hashlib.sha1(query.lower().encode()).hexdigest()[:20]
        cached = self.cache_dir / f"{key}.json"
        if cached.exists() and time.time() - cached.stat().st_mtime < CACHE_TTL_S:
            return json.loads(cached.read_text(encoding="utf-8"))["suggestions"]
        params = urllib.parse.urlencode({"client": "firefox", "ds": "yt", "hl": "en", "gl": "us", "q": query})
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            self.sleep(wait)
        try:
            payload = json.loads(self.fetch(f"{SUGGEST_URL}?{params}").decode("utf-8", errors="replace"))
            suggestions = [s for s in payload[1] if isinstance(s, str)]
        except Exception as exc:  # unofficial endpoint: never fatal
            log.warning("suggest lookup failed for %r: %s", query, exc)
            return []
        finally:
            self._last_request = time.monotonic()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"query": query, "suggestions": suggestions}), encoding="utf-8")
        return suggestions


def _tokens(text: str) -> set[str]:
    return {t for t in text.lower().replace("&", " ").split() if len(t) > 2}


# Words allowed in a researched term besides the recipe's own vocabulary. A whitelist, because
# suggestions carry misleading claims ("black screen", "no ads") and news/music noise that would
# make metadata deceptive under YouTube's spam policies.
GENERIC_WORDS = {
    "for", "and", "the", "with", "to", "of", "in", "on", "sound", "sounds", "ambience", "ambient", "nature", "noise",
    "sleep", "sleeping", "asleep", "fall", "fast", "deep", "study", "studying", "focus", "work", "relax", "relaxing",
    "relaxation", "meditation", "calm", "calming", "soothing", "gentle", "soft", "heavy", "light", "night", "hours",
    "hour", "long", "real", "natural", "peaceful", "cozy", "background", "white", "brown", "pink", "insomnia",
    "stress", "relief", "anxiety", "baby", "tropical", "costa", "rica", "rainforest", "jungle", "forest",
}


def rank_terms(seeds: list[str], relevant_words: set[str], client: SuggestClient, blocked_words: set[str] = frozenset()) -> list[RankedTerm]:
    """Expand each seed with its suggestions. Earlier suggestions are more popular; terms that
    appear for several seeds score higher. A term is kept only if every word describes the
    ambience or its use (recipe vocabulary or GENERIC_WORDS), it is English and not blocked."""
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    relevant = {w.lower() for w in relevant_words}
    for seed in seeds:
        seed_l = seed.lower().strip()
        scores[seed_l] = scores.get(seed_l, 0.0) + 1.0
        sources.setdefault(seed_l, set()).add("seed")
        for position, suggestion in enumerate(client.suggestions(seed_l)):
            term = " ".join(suggestion.lower().split())
            words = set(re.findall(r"[a-z]+", term)) - {"a", "an"}
            if re.search(r"\d", term) and not re.search(r"\b\d+\s*hours?\b", term):
                continue
            allowed = relevant | GENERIC_WORDS
            if not words & relevant or not words <= allowed or words & blocked_words or not is_english_only(term) or len(term) > 60:
                continue
            scores[term] = scores.get(term, 0.0) + 1.0 / (1 + position * 0.35)
            sources.setdefault(term, set()).add(seed_l)
    ranked = [RankedTerm(term, round(score, 3), tuple(sorted(sources[term]))) for term, score in scores.items()]
    return sorted(ranked, key=lambda r: (-r.score, r.term))
