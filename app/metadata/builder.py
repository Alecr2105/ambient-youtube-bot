from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.audio.recipe import Recipe
from app.metadata.language import spanish_findings
from app.metadata.titles import choose_title, duration_label
from app.research.suggest import RankedTerm
from app.youtube.uploader import MAX_TAGS_CHARS, VideoMetadata, _tags_length

USES = {
    "sleep": "falling asleep and staying asleep",
    "study": "studying, reading and deep focus",
    "relaxation": "relaxation, meditation and stress relief",
}


@dataclass
class MetadataPackage:
    title: str
    description: str
    tags: list[str]
    category_id: str
    default_language: str
    default_audio_language: str
    title_candidates: list[dict]
    research_terms: list[dict]
    localizations: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_video_metadata(self, contains_synthetic_media: bool) -> VideoMetadata:
        return VideoMetadata(self.title, self.description, self.tags, self.category_id, self.default_language,
                             self.default_audio_language, contains_synthetic_media=contains_synthetic_media,
                             localizations=self.localizations)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    def spanish_problems(self) -> dict[str, list[str]]:
        fields = {"title": self.title, "description": self.description, "tags": " ".join(self.tags)}
        return {name: found for name, text in fields.items() if (found := spanish_findings(text))}


def build_description(recipe: Recipe, minutes: float, filmed_in_costa_rica: bool, attribution: str) -> str:
    duration = duration_label(minutes)
    parts = [USES[s] for s in recipe.subniches]
    uses = parts[0] if len(parts) == 1 else "; ".join(parts[:-1]) + "; or " + parts[-1]
    lines = [
        f"{recipe.name} — {duration} of continuous ambience with no music and no talking.",
        "",
        f"Use it for {uses}. Let it play in the background, turn the screen off, and let the sound do the work.",
    ]
    if filmed_in_costa_rica:
        lines += ["", "All footage was filmed on location in Costa Rica by the channel. Every video is mixed individually, so no two are the same."]
    else:
        lines += ["", "Every video is mixed individually, so no two are the same."]
    lines += ["", "If this helped you sleep, study or unwind, subscribe for a new ambience every day."]
    if attribution:
        lines += ["", attribution]
    hashtags = [f"#{w.replace(' ', '')}" for w in recipe.keywords[:3]]
    lines += ["", " ".join(hashtags)]
    return "\n".join(lines)


def build_tags(recipe: Recipe, research: list[RankedTerm], filmed_in_costa_rica: bool, minutes: float | None = None) -> list[str]:
    ordered = [t.term for t in research] + [k.lower() for k in recipe.keywords] + recipe.visual_tags
    if filmed_in_costa_rica:
        ordered.append("costa rica")
    real_duration = duration_label(minutes).lower() if minutes else None
    tags: list[str] = []
    for tag in ordered:
        tag = " ".join(tag.lower().split())
        if not tag or tag in tags or spanish_findings(tag):
            continue
        durations = re.findall(r"\d+(?:\.\d)?\s*hours?", tag)
        if durations and any(" ".join(d.split()) != real_duration for d in durations):
            continue  # a tag must not claim a length the video does not have
        if _tags_length([*tags, tag]) > MAX_TAGS_CHARS:
            continue
        tags.append(tag)
    return tags


PURPOSE_ES = {"sleep": "dormir profundamente", "study": "estudiar y concentrarse", "relaxation": "relajarse y meditar"}


def spanish_localization(recipe: Recipe, minutes: float, filmed_in_costa_rica: bool) -> dict[str, dict[str, str]]:
    """Alternate 'es' track (YouTube shows it to Spanish-language viewers). Main fields stay English."""
    if not recipe.name_es:
        return {}
    hours = minutes / 60
    duration = f"{int(round(hours))} horas" if abs(hours - round(hours)) < 0.05 else f"{hours:.1f}".rstrip("0").rstrip(".") + " horas"
    purpose = PURPOSE_ES[recipe.subniches[0]]
    title = f"{recipe.name_es} para {purpose} | {duration}"[:100]
    lines = [f"{recipe.name_es}: {duration} de ambiente continuo, sin música y sin voces.", "", f"Ideal para {purpose}."]
    if filmed_in_costa_rica and recipe.costa_rica_eligible:
        lines += ["", "Imágenes grabadas en Costa Rica por el canal."]
    return {"es": {"title": title, "description": "\n".join(lines)}}


def build_metadata(
    recipe: Recipe,
    minutes: float,
    research: list[RankedTerm],
    recent_titles: list[str],
    seed: int,
    category_id: str,
    filmed_in_costa_rica: bool,
    attribution: str = "",
    enable_es_localization: bool = False,
) -> MetadataPackage:
    chosen, ranked = choose_title(recipe, minutes, research, recent_titles, seed)
    package = MetadataPackage(
        title=chosen.text,
        description=build_description(recipe, minutes, filmed_in_costa_rica and recipe.costa_rica_eligible, attribution),
        tags=build_tags(recipe, research, filmed_in_costa_rica and recipe.costa_rica_eligible, minutes),
        category_id=category_id,
        default_language="en",
        default_audio_language="en",
        title_candidates=[{"text": s.text, "score": s.score, "breakdown": s.breakdown} for s in ranked[:10]],
        research_terms=[{"term": t.term, "score": t.score, "sources": list(t.sources)} for t in research[:30]],
        localizations=spanish_localization(recipe, minutes, filmed_in_costa_rica) if enable_es_localization else {},
    )
    problems = package.spanish_problems()
    if problems:
        raise ValueError(f"Spanish text in main YouTube fields: {problems}")
    return package
