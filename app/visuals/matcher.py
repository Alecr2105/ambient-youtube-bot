from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Visual

MAX_VISUALS = 3
# Photos bring no movement of their own, so changing picture is the only variety a video has:
# when there is no footage to cut, use more of them.
MAX_IMAGE_VISUALS = 8


class NoMatchingVisualsError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualChoice:
    id: int
    path: str
    type: str
    duration: float | None
    width: int
    height: int
    allow_mirror: bool
    tags: tuple[str, ...]
    score: float


def score_visual(visual: Visual, wanted: set[str], now: datetime) -> float:
    overlap = len(wanted & set(visual.tags))
    if overlap == 0:
        return 0.0
    score = overlap * 10.0
    if visual.type == "video":
        score += 5.0
    score -= min(visual.usage_count, 20) * 0.25
    if visual.last_used_at is not None:
        last = visual.last_used_at if visual.last_used_at.tzinfo else visual.last_used_at.replace(tzinfo=UTC)
        days = (now - last).total_seconds() / 86400
        score -= max(0.0, 7 - days)  # rest a clip for about a week
    return score


def choose_visuals(session: Session, visual_tags: list[str], now: datetime | None = None, limit: int | None = None) -> list[VisualChoice]:
    now = now or datetime.now(UTC)
    wanted = {t.lower() for t in visual_tags}
    rows = session.scalars(select(Visual).where(Visual.missing.is_(False))).all()
    scored = sorted(((score_visual(v, wanted, now), v) for v in rows), key=lambda item: (-item[0], item[1].id))
    matches = [(s, v) for s, v in scored if s > 0]
    if not matches:
        raise NoMatchingVisualsError(f"no indexed visual matches tags {sorted(wanted)}")
    videos = [(s, v) for s, v in matches if v.type == "video"]
    picked = videos[: limit or MAX_VISUALS] if videos else matches[: limit or MAX_IMAGE_VISUALS]
    return [
        VisualChoice(v.id, v.path, v.type, v.duration, v.width, v.height, v.allow_mirror, tuple(v.tags), s)
        for s, v in picked
    ]


def mark_used(session: Session, visual_ids: list[int], now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for visual in session.scalars(select(Visual).where(Visual.id.in_(visual_ids))):
        visual.usage_count += 1
        visual.last_used_at = now
