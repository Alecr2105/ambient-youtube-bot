"""Which recipes can already be produced with the indexed footage, and what is still missing.

`choose_plan` silently skips any recipe without matching footage, so with an empty
`assets/visuals/` the daily selector simply finds nothing. This module answers the
practical question instead: what should the channel owner film next?
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.recipe import Recipe, all_recipes
from app.database.models import Visual
from app.visuals.matcher import MAX_VISUALS

# A 45-150 s segment is cut from a clip and no region is reused inside a video, so a few
# minutes per recipe is the difference between real variety and the same shot again and again.
COMFORTABLE_SECONDS = 600.0
COMFORTABLE_CLIPS = 2

# The renderer scales every visual to *cover* the output frame (and with VISUAL_MOTION=full
# zooms in on top of that), so anything smaller than the frame is enlarged and looks soft for hours.
MIN_PHOTO_WIDTH = 1920
MIN_PHOTO_HEIGHT = 1080


def is_small(visual: Visual) -> bool:
    return visual.width < MIN_PHOTO_WIDTH or visual.height < MIN_PHOTO_HEIGHT


@dataclass(frozen=True)
class RecipeCoverage:
    recipe: Recipe
    matched: tuple[str, ...]
    matched_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    video_clips: int
    video_seconds: float
    photos: int
    small_photos: tuple[str, ...]
    loops: int = 0

    @property
    def ready(self) -> bool:
        """Exactly the condition the daily selector applies: at least one matching visual."""
        return bool(self.matched)

    @property
    def thin(self) -> bool:
        if self.loops >= COMFORTABLE_CLIPS:
            return False  # animated scenes repeat by design; two of them already alternate
        return self.ready and (self.video_clips < COMFORTABLE_CLIPS or self.video_seconds < COMFORTABLE_SECONDS)

    def summary(self) -> str:
        parts = []
        if self.video_clips:
            parts.append(f"{self.video_clips} clip(s), {self.video_seconds / 60:.0f} min of video")
        if self.loops:
            parts.append(f"{self.loops} animated scene(s)")
        if self.photos:
            parts.append(f"{self.photos} photo(s)")
        return ", ".join(parts) or "nothing"


def recipe_coverage(session: Session, recipes: list[Recipe] | None = None) -> list[RecipeCoverage]:
    visuals = session.scalars(select(Visual).where(Visual.missing.is_(False))).all()
    report = []
    for recipe in recipes if recipes is not None else all_recipes():
        if not recipe.enabled:
            continue
        wanted = {tag.lower() for tag in recipe.visual_tags}
        matched = [v for v in visuals if wanted & {t.lower() for t in v.tags}]
        covered = {t.lower() for v in matched for t in v.tags} & wanted
        videos = [v for v in matched if v.type == "video"]
        photos = [v for v in matched if v.type == "image"]
        loops = [v for v in matched if v.type == "loop"]
        report.append(
            RecipeCoverage(
                recipe=recipe,
                matched=tuple(v.path for v in matched),
                matched_tags=tuple(sorted(covered)),
                missing_tags=tuple(sorted(wanted - covered)),
                video_clips=len(videos),
                video_seconds=float(sum(v.duration or 0.0 for v in videos)),
                photos=len(photos),
                small_photos=tuple(v.path for v in photos + loops if is_small(v)),
                loops=len(loops),
            )
        )
    return report


def tag_demand(report: list[RecipeCoverage]) -> list[tuple[str, int]]:
    """Missing tags of the recipes that cannot be produced yet, most valuable first."""
    counts: dict[str, int] = {}
    for coverage in report:
        if coverage.ready:
            continue
        for tag in coverage.missing_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def render_report(report: list[RecipeCoverage], visuals_dir: str) -> str:
    total_clips = len({path for coverage in report for path in coverage.matched})
    lines = [f"footage in {visuals_dir}: {total_clips} indexed file(s) matching an enabled recipe", ""]
    width = max((len(c.recipe.slug) for c in report), default=10)
    for coverage in report:
        if not coverage.ready:
            status, detail = "NO FOOTAGE", f"needs any of: {', '.join(coverage.recipe.visual_tags)}"
        elif coverage.thin:
            status, detail = "THIN", f"{coverage.summary()}; film more for variety"
        else:
            status, detail = "READY", coverage.summary()
        if coverage.ready and coverage.missing_tags:
            detail += f"; no footage yet for: {', '.join(coverage.missing_tags)}"
        lines.append(f"[{status:^10}] {coverage.recipe.slug:<{width}}  {detail}")
    ready = [c for c in report if c.ready]
    lines += ["", f"{len(ready)} of {len(report)} enabled recipes can be produced today."]
    small = sorted({path for coverage in report for path in coverage.small_photos})
    if small:
        lines += [
            "",
            f"{len(small)} picture(s) are smaller than {MIN_PHOTO_WIDTH}x{MIN_PHOTO_HEIGHT}. The renderer enlarges them to "
            "cover the frame, so they will look softer. Replace them with larger versions when you can:",
        ]
        lines += [f"  {Path(path).name}" for path in small]
    demand = tag_demand(report)
    if demand:
        best = ", ".join(f"{tag} ({count})" for tag, count in demand[:8])
        lines += [
            "",
            "Filming clips with these tags unlocks the most recipes (tag: recipes waiting):",
            f"  {best}",
            f"Tag the files in assets/visuals/visuals.yaml, then run: python main.py index-visuals",
            f"Aim for {COMFORTABLE_CLIPS}+ clips and {COMFORTABLE_SECONDS / 60:.0f}+ minutes per ambient; "
            f"up to {MAX_VISUALS} clips are used in one video.",
        ]
    return "\n".join(lines)
