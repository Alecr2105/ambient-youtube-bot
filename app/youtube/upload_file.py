"""Uploads one existing file to the channel. Used for one-off videos such as the audit screencast."""

from __future__ import annotations

import logging
from pathlib import Path

from app.database.session import make_engine
from app.utils.config import Settings
from app.youtube.auth import build_service, load_credentials
from app.youtube.quota import QuotaTracker
from app.youtube.uploader import VideoMetadata, get_video, upload_video

log = logging.getLogger(__name__)


def upload_single_file(
    settings: Settings,
    path: Path,
    title: str,
    description: str,
    privacy: str,
    tags: list[str] | None = None,
) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    meta = VideoMetadata(
        title=title,
        description=description,
        tags=tags or [],
        category_id=settings.youtube_category_id,
        privacy_status=privacy,
        contains_synthetic_media=settings.declare_synthetic_media,
    )
    engine = make_engine(settings.database_url)
    quota = QuotaTracker(engine, settings)
    service = build_service(load_credentials(settings))
    try:
        response = upload_video(service, path, meta, quota, progress=lambda p: log.info("upload %.0f%%", p * 100))
        details = get_video(service, response["id"], quota)
        status = (details or response).get("status", {})
        return {
            "youtube_video_id": response["id"],
            "url": f"https://www.youtube.com/watch?v={response['id']}",
            "studio_url": f"https://studio.youtube.com/video/{response['id']}/edit",
            "privacy_status": status.get("privacyStatus"),
            "quota_used_today": {bucket: quota.used(bucket) for bucket in ("uploads", "general")},
        }
    finally:
        engine.dispose()
