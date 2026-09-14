"""Keeps local state in step with YouTube: SCHEDULED -> PUBLISHED and view counts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import PublishHistory, Video, YoutubeResult
from app.database.repository import transition
from app.database.session import session_scope
from app.database.states import VideoState
from app.youtube.quota import QuotaTracker

log = logging.getLogger(__name__)

BATCH = 50  # videos.list accepts up to 50 ids per call (1 unit)


def _latest_results(session: Session) -> dict[str, str]:
    rows = session.execute(select(YoutubeResult.video_id, YoutubeResult.youtube_video_id).order_by(YoutubeResult.id)).all()
    return {video_id: yt_id for video_id, yt_id in rows if yt_id}


def sync_status(service, engine, quota: QuotaTracker) -> dict[str, int]:
    with session_scope(engine) as session:
        mapping = _latest_results(session)
        tracked = {
            v.id: mapping[v.id]
            for v in session.scalars(select(Video).where(Video.state.in_([VideoState.SCHEDULED, VideoState.PUBLISHED])))
            if v.id in mapping
        }
    counts = {"published": 0, "stats_updated": 0, "missing": 0}
    ids = list(tracked.items())
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        quota.ensure("videos.list")
        response = service.videos().list(part="status,statistics", id=",".join(yt for _, yt in chunk), maxResults=BATCH).execute()
        quota.record("videos.list")
        items = {item["id"]: item for item in response.get("items", [])}
        with session_scope(engine) as session:
            for video_id, yt_id in chunk:
                item = items.get(yt_id)
                if item is None:
                    counts["missing"] += 1
                    log.warning("video %s (%s) no longer found on YouTube", video_id, yt_id)
                    continue
                video = session.get(Video, video_id)
                if video.state is VideoState.SCHEDULED and item.get("status", {}).get("privacyStatus") == "public":
                    transition(session, video, VideoState.PUBLISHED, "detected public on YouTube")
                    counts["published"] += 1
                views = item.get("statistics", {}).get("viewCount")
                if views is not None:
                    for history in session.scalars(select(PublishHistory).where(PublishHistory.video_id == video_id)):
                        history.views = int(views)
                        history.stats_updated_at = datetime.now(UTC)
                    counts["stats_updated"] += 1
    return counts
