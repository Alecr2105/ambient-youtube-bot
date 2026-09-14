from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError, ResumableUploadError
from googleapiclient.http import MediaFileUpload

from app.youtube.quota import QuotaTracker

log = logging.getLogger(__name__)

MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_BYTES = 5000
MAX_TAGS_CHARS = 500
CHUNK_SIZE = 16 * 1024 * 1024
RETRIABLE_STATUS = {500, 502, 503, 504}


class MetadataError(ValueError):
    pass


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    default_language: str = "en"
    default_audio_language: str = "en"
    privacy_status: str = "private"
    publish_at: datetime | None = None
    contains_synthetic_media: bool = False
    localizations: dict[str, dict[str, str]] = field(default_factory=dict)


def _tags_length(tags: list[str]) -> int:
    # YouTube counts quotes around tags that contain spaces, plus the separating commas.
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(len(tags) - 1, 0)


def build_video_body(meta: VideoMetadata, now: datetime | None = None) -> dict[str, Any]:
    if not meta.title.strip() or len(meta.title) > MAX_TITLE_CHARS:
        raise MetadataError(f"title must be 1-{MAX_TITLE_CHARS} characters")
    if any(ch in meta.title + meta.description for ch in "<>"):
        raise MetadataError("title and description cannot contain < or >")
    if len(meta.description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise MetadataError(f"description exceeds {MAX_DESCRIPTION_BYTES} bytes")
    if _tags_length(meta.tags) > MAX_TAGS_CHARS:
        raise MetadataError(f"tags exceed {MAX_TAGS_CHARS} characters")
    if meta.privacy_status not in ("private", "unlisted", "public"):
        raise MetadataError(f"invalid privacy status {meta.privacy_status}")

    status: dict[str, Any] = {
        "privacyStatus": meta.privacy_status,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": meta.contains_synthetic_media,
    }
    if meta.publish_at is not None:
        if meta.publish_at.tzinfo is None:
            raise MetadataError("publish_at must be timezone-aware")
        if meta.privacy_status != "private":
            raise MetadataError("publishAt can only be set on private videos")
        if meta.publish_at <= (now or datetime.now(UTC)):
            raise MetadataError("publish_at must be in the future (a past time publishes immediately)")
        status["publishAt"] = meta.publish_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    body: dict[str, Any] = {
        "snippet": {
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "categoryId": meta.category_id,
            "defaultLanguage": meta.default_language,
            "defaultAudioLanguage": meta.default_audio_language,
        },
        "status": status,
    }
    if meta.localizations:
        body["localizations"] = meta.localizations
    return body


def _retriable(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        return exc.resp.status in RETRIABLE_STATUS
    return isinstance(exc, ResumableUploadError | ConnectionError | TimeoutError | OSError)


def upload_video(
    service,
    path: Path,
    meta: VideoMetadata,
    quota: QuotaTracker,
    max_retries: int = 8,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    body = build_video_body(meta)
    quota.ensure("videos.insert")
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=CHUNK_SIZE, resumable=True)
    request = service.videos().insert(part=",".join(body), body=body, media_body=media)
    response = None
    retries = 0
    quota.record("videos.insert")  # quota is charged per call, even if the upload later fails
    while response is None:
        try:
            status, response = request.next_chunk()
            retries = 0
            if status is not None and progress is not None:
                progress(status.progress())
        except Exception as exc:
            if not _retriable(exc) or retries >= max_retries:
                raise
            retries += 1
            delay = min(2**retries, 300) * (0.5 + random.random() / 2)
            log.warning("upload chunk failed (%s); retry %d/%d in %.0f s", exc, retries, max_retries, delay)
            sleep(delay)
    log.info("uploaded video %s", response.get("id"))
    return response


def set_thumbnail(service, youtube_video_id: str, image: Path, quota: QuotaTracker) -> dict[str, Any]:
    quota.ensure("thumbnails.set")
    media = MediaFileUpload(str(image), mimetype="image/jpeg")
    response = service.thumbnails().set(videoId=youtube_video_id, media_body=media).execute()
    quota.record("thumbnails.set")
    return response


def get_video(service, youtube_video_id: str, quota: QuotaTracker) -> dict[str, Any] | None:
    quota.ensure("videos.list")
    response = service.videos().list(part="snippet,status,processingDetails", id=youtube_video_id).execute()
    quota.record("videos.list")
    items = response.get("items", [])
    return items[0] if items else None


def my_channel(service, quota: QuotaTracker) -> dict[str, Any] | None:
    quota.ensure("channels.list")
    response = service.channels().list(part="snippet", mine=True).execute()
    quota.record("channels.list")
    items = response.get("items", [])
    return items[0] if items else None
