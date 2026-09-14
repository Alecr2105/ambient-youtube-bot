from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Title, Video
from app.quality.report import QualityReport

TITLE_SIMILARITY_LIMIT = 0.9
TITLE_WINDOW_DAYS = 120
CONCEPT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Combination:
    recipe: str
    sound_keys: tuple[str, ...]  # catalog sound ids or "procedural:<generator>"
    visual_ids: tuple[int, ...]
    title: str

    def normalized_title(self) -> str:
        return re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()

    def hash(self) -> str:
        payload = json.dumps(
            {"recipe": self.recipe, "sounds": sorted(self.sound_keys), "visuals": sorted(self.visual_ids), "title": self.normalized_title()},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def fingerprint(self) -> dict:
        return {"recipe": self.recipe, "visuals": sorted(self.visual_ids), "sounds": sorted(self.sound_keys)}


def recent_concept_repeats(session: Session, recipe: str, visual_ids: list[int], publish_date: date, exclude_video_id: str | None = None) -> list[str]:
    """Videos publishing within the concept window of `publish_date` with this recipe and exactly this footage."""
    window = timedelta(days=CONCEPT_WINDOW_DAYS)
    videos = session.scalars(
        select(Video).where(
            Video.target_publish_date > publish_date - window,
            Video.target_publish_date < publish_date + window,
            Video.concept_fingerprint.is_not(None),
        )
    ).all()
    wanted = sorted(visual_ids)
    return [
        v.id for v in videos
        if v.id != exclude_video_id and v.concept_fingerprint.get("recipe") == recipe and v.concept_fingerprint.get("visuals") == wanted
    ]


def check_duplicates(session: Session, combination: Combination, publish_date: date, exclude_video_id: str | None = None, now: datetime | None = None) -> QualityReport:
    now = now or datetime.now(UTC)
    report = QualityReport("duplicates")
    digest = combination.hash()

    query = select(Video.id).where(Video.combination_hash == digest)
    if exclude_video_id:
        query = query.where(Video.id != exclude_video_id)
    existing = session.scalar(query)
    report.add("combination_unique", existing is None, existing, None, "exact recipe + sounds + visuals + title already used")

    recent = session.execute(
        select(Title.text, Title.video_id).join(Video, Video.id == Title.video_id)
        .where(Title.selected.is_(True), Title.created_at >= now - timedelta(days=TITLE_WINDOW_DAYS))
    ).all()
    title = combination.normalized_title()
    similar = [
        (text, round(ratio, 3))
        for text, video_id in recent
        if video_id != exclude_video_id
        and (ratio := difflib.SequenceMatcher(None, title, re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()).ratio()) >= TITLE_SIMILARITY_LIMIT
    ]
    report.add("title_not_near_duplicate", not similar, similar[:3], TITLE_SIMILARITY_LIMIT)

    repeats = recent_concept_repeats(session, combination.recipe, list(combination.visual_ids), publish_date, exclude_video_id)
    report.add("concept_not_repeated", not repeats, repeats, f"same recipe and footage within {CONCEPT_WINDOW_DAYS} days")
    report.metrics["combination_hash"] = digest
    return report
