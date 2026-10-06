"""Planning, uploading and the long-running scheduler."""

from __future__ import annotations

import json
import logging
import secrets
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.audio.recipe import RECIPES_DIR, load_recipe
from app.database.models import Command, PublishHistory, Video, YoutubeResult
from app.database.repository import transition
from app.database.session import session_scope
from app.database.states import VideoState
from app.metadata.builder import MetadataPackage
from app.scheduler.pipeline import _recipe_slug, run_production
from app.scheduler.selector import choose_plan, recipe_row
from app.utils.config import WEEKDAYS, Mode, Settings
from app.visuals.matcher import NoMatchingVisualsError

log = logging.getLogger(__name__)

ACTIVE_STATES = [s for s in VideoState if s not in (VideoState.FAILED, VideoState.PUBLISHED)]
MAX_RECIPE_ATTEMPTS = 3


def is_paused(session) -> bool:
    last = session.scalar(select(Command).where(Command.name.in_(["pause", "resume"])).order_by(Command.id.desc()).limit(1))
    # pause/resume take effect when written; they are state, not work for the worker.
    return last is not None and last.name == "pause"


def new_video(settings: Settings, engine, day: date, recipe_slug: str | None = None, exclude: set[str] = frozenset()) -> str:
    with session_scope(engine) as session:
        plan = choose_plan(session, settings, day, exclude=exclude)
        recipe = load_recipe(recipe_slug) if recipe_slug else plan.recipe
        row = recipe_row(session, recipe, str(RECIPES_DIR / f"{recipe.slug}.yaml"))
        video_id = f"{day:%Y%m%d}_{recipe.slug}_{secrets.token_hex(2)}"
        session.add(Video(
            id=video_id, recipe_id=row.id, mode=settings.mode.value, seed=plan.seed, duration_seconds=plan.duration_seconds,
            target_publish_date=day, publish_at=plan.publish_at.astimezone(UTC),
        ))
    log.info("planned %s for %s (publish %s)", video_id, day, plan.publish_at.isoformat())
    return video_id


def dates_needing_videos(settings: Settings, engine, today: date) -> list[date]:
    active_days = {WEEKDAYS.index(d) for d in settings.active_weekdays}
    horizon = [today + timedelta(days=i) for i in range(settings.buffer_days + 1)]
    wanted = [d for d in horizon if d.weekday() in active_days]
    with session_scope(engine) as session:
        covered = {}
        # A test video (MODE=test, e.g. trying a new format) never stands in for a real day.
        for video in session.scalars(select(Video).where(Video.target_publish_date.in_(wanted), Video.state.in_(ACTIVE_STATES + [VideoState.PUBLISHED]),
                                                         Video.mode == settings.mode.value)):
            covered[video.target_publish_date] = covered.get(video.target_publish_date, 0) + 1
    return [d for d in wanted for _ in range(max(0, settings.videos_per_day - covered.get(d, 0)))]


def produce_for_day(settings: Settings, engine, day: date, recipe_slug: str | None = None) -> str | None:
    """Plan and produce one video for `day`; on content failure try another recipe."""
    tried: set[str] = set()
    for _ in range(MAX_RECIPE_ATTEMPTS):
        try:
            video_id = new_video(settings, engine, day, recipe_slug, exclude=tried)
        except NoMatchingVisualsError as exc:
            log.error("cannot plan a video for %s: %s", day, exc)
            return None
        state = run_production(settings, engine, video_id)
        if state is VideoState.READY:
            return video_id
        tried.add(video_id.split("_", 1)[1].rsplit("_", 1)[0])
        if recipe_slug:
            return None
    return None


UPLOAD_PROGRESS_FILE = "upload_progress.json"


class UploadProgress:
    """Logs the upload every 5 % and keeps the latest figure next to the video, where
    `main.py videos` and the dashboard read it (a 1-2 GB upload takes 20-40 minutes)."""

    STEP = 5

    def __init__(self, out: Path, video_id: str):
        self.path, self.video_id, self.logged = out / UPLOAD_PROGRESS_FILE, video_id, -self.STEP
        self(0.0)

    def __call__(self, fraction: float) -> None:
        percent = int(max(0.0, min(fraction, 1.0)) * 100)
        if percent - self.logged < self.STEP and percent < 100:
            return
        self.logged = percent
        log.info("%s: upload %d%%", self.video_id, percent)
        self.path.write_text(json.dumps({"percent": percent, "updated_at": datetime.now(UTC).isoformat()}), encoding="utf-8")


def upload_percent(output_path: str | None) -> int | None:
    """Latest upload progress of a video, or None if it has never started uploading."""
    if not output_path:
        return None
    path = Path(output_path) / UPLOAD_PROGRESS_FILE
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["percent"])
    except (OSError, ValueError, KeyError):
        return None


def upload_ready(settings: Settings, engine, video_id: str) -> VideoState:
    from app.youtube.auth import build_service, load_credentials
    from app.youtube.quota import QuotaTracker
    from app.youtube.uploader import set_thumbnail, upload_video

    if settings.mode is not Mode.PRODUCTION:
        raise RuntimeError("uploads only run with MODE=production")
    quota = QuotaTracker(engine, settings)
    # Checked before leaving READY: an exhausted quota or missing token means "try later", not failure.
    quota.ensure("videos.insert")
    service = build_service(load_credentials(settings))
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        if video.state is not VideoState.READY:
            raise RuntimeError(f"{video_id} is {video.state}, not READY")
        publish_at = video.publish_at
        out = Path(video.output_path)
        transition(session, video, VideoState.UPLOADING)
    package = MetadataPackage(**json.loads((out / "metadata.json").read_text(encoding="utf-8")))
    meta = package.to_video_metadata(settings.declare_synthetic_media)
    now = datetime.now(UTC)
    if publish_at is not None:
        publish_at = publish_at if publish_at.tzinfo else publish_at.replace(tzinfo=UTC)
        if publish_at > now + timedelta(minutes=15):
            meta.publish_at = publish_at
        else:
            # The slot has passed (late production, catching up after a restart). Scheduling into
            # the past is rejected and a plain private upload would never go live: publish it now.
            meta.publish_at = None
            meta.privacy_status = "public"
            log.info("%s: publish time %s has passed; publishing on upload", video_id, publish_at.isoformat())
    progress = UploadProgress(out, video_id)
    try:
        response = upload_video(service, out / "video.mp4", meta, quota, progress=progress)
        progress(1.0)
    except Exception as exc:
        with session_scope(engine) as session:
            transition(session, session.get(Video, video_id), VideoState.FAILED, f"upload: {exc}"[:2000])
        raise
    thumb_note = "ok"
    try:
        set_thumbnail(service, response["id"], out / "thumb_selected.jpg", quota)
    except Exception as exc:  # custom thumbnails need a verified channel; the video itself is fine
        thumb_note = f"thumbnail not set: {exc}"
        log.warning("%s: %s", video_id, thumb_note)
    if settings.youtube_playlist_id:
        try:
            quota.ensure("playlistItems.insert")
            service.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": settings.youtube_playlist_id, "resourceId": {"kind": "youtube#video", "videoId": response["id"]}}}).execute()
            quota.record("playlistItems.insert", video_id)
        except Exception as exc:
            log.warning("%s: playlist insert failed: %s", video_id, exc)
    with session_scope(engine) as session:
        session.add(YoutubeResult(video_id=video_id, youtube_video_id=response["id"], url=f"https://www.youtube.com/watch?v={response['id']}",
                                  privacy_status=response.get("status", {}).get("privacyStatus"), publish_at=meta.publish_at,
                                  request_body={"title": meta.title, "thumbnail": thumb_note}, response=response))
        video = session.get(Video, video_id)
        transition(session, video, VideoState.SCHEDULED, thumb_note)
        slug = _recipe_slug(session, video)
        session.add(PublishHistory(video_id=video_id, ambient_slug=slug, subniche=load_recipe(slug).subniches[0], published_on=video.target_publish_date))
    return VideoState.SCHEDULED


def cleanup_outputs(settings: Settings, engine, today: date) -> list[str]:
    """Delete big media of long-published videos; metadata and reports are always kept."""
    removed = []
    cutoff = today - timedelta(days=settings.retention_days)
    with session_scope(engine) as session:
        for video in session.scalars(select(Video).where(Video.state.in_([VideoState.SCHEDULED, VideoState.PUBLISHED]), Video.target_publish_date < cutoff)):
            if not video.output_path:
                continue
            for name in ("video.mp4", "audio.flac"):
                path = Path(video.output_path) / name
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
    return removed


def daily_cycle(settings: Settings, engine) -> None:
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    with session_scope(engine) as session:
        if is_paused(session):
            log.info("automation paused; skipping daily cycle")
            return
    free_gb = shutil.disk_usage(settings.output_dir if settings.output_dir.exists() else settings.output_dir.parent).free / 1024**3
    if free_gb < settings.min_free_disk_gb:
        log.error("only %.0f GB free (min %.0f); not producing", free_gb, settings.min_free_disk_gb)
        return
    cleanup_outputs(settings, engine, today)
    # Upload every video as soon as it is ready, not after the whole buffer is produced: on a
    # catch-up (first start, a restart, a power cut) today's video would otherwise wait hours.
    upload_ready_videos(settings, engine)
    with session_scope(engine) as session:
        unfinished = [v.id for v in session.scalars(select(Video).where(Video.state.not_in([VideoState.READY, VideoState.UPLOADING, VideoState.SCHEDULED, VideoState.PUBLISHED, VideoState.FAILED])))]
    for video_id in unfinished:
        run_production(settings, engine, video_id)
        upload_ready_videos(settings, engine)
    for day in dates_needing_videos(settings, engine, today):
        produce_for_day(settings, engine, day)
        upload_ready_videos(settings, engine)
    feed_live_stream(settings, engine)
    if settings.mode is Mode.PRODUCTION:
        try:
            from app.youtube.auth import build_service, load_credentials
            from app.youtube.quota import QuotaTracker
            from app.youtube.sync import sync_status

            log.info("youtube sync: %s", sync_status(build_service(load_credentials(settings)), engine, QuotaTracker(engine, settings)))
        except Exception as exc:
            log.warning("youtube status sync skipped: %s", exc)


def feed_live_stream(settings: Settings, engine) -> None:
    """Adds the new videos to the 24/7 live stream; never stops the daily cycle."""
    from app.stream.sync import sync_stream

    try:
        sync_stream(settings, engine)
    except Exception as exc:
        log.error("live stream sync failed: %s", exc)


def upload_ready_videos(settings: Settings, engine) -> None:
    """Uploads READY videos, earliest publish date first; stops at the first failure (quota, network)."""
    if settings.mode is not Mode.PRODUCTION:
        return
    with session_scope(engine) as session:
        # Only videos produced in production mode: a test video is for review, never published by the worker.
        ready = [v.id for v in session.scalars(select(Video).where(Video.state == VideoState.READY, Video.mode == Mode.PRODUCTION.value)
                                                .order_by(Video.target_publish_date))]
    for video_id in ready:
        try:
            upload_ready(settings, engine, video_id)
        except Exception as exc:
            log.error("upload of %s failed: %s", video_id, exc)
            break


def process_commands(settings: Settings, engine) -> None:
    """Commands queued by the dashboard. The dashboard never runs production itself."""
    with session_scope(engine) as session:
        pending = [(c.id, c.name, c.payload) for c in session.scalars(select(Command).where(Command.status == "pending").order_by(Command.id))]
        for command_id, _name, _payload in pending:
            session.get(Command, command_id).status = "running"
    for command_id, name, payload in pending:
        result = "ok"
        try:
            if name == "produce_now":
                day = date.fromisoformat(payload["date"]) if payload.get("date") else datetime.now(ZoneInfo(settings.timezone)).date()
                result = str(produce_for_day(settings, engine, day, payload.get("recipe")))
            elif name == "resume_video":
                result = run_production(settings, engine, payload["video_id"]).value
            elif name == "upload_video":
                result = upload_ready(settings, engine, payload["video_id"]).value
            elif name == "reindex_visuals":
                from app.utils import ffmpeg
                from app.visuals.indexer import index_visuals

                with session_scope(engine) as session:
                    report = index_visuals(session, settings.visuals_dir, ffmpeg.require_binary("ffprobe", settings.ffprobe_path))
                result = f"added {len(report.added)}, updated {len(report.updated)}, missing {len(report.missing)}"
            status = "done"
        except Exception as exc:
            status, result = "failed", f"{type(exc).__name__}: {exc}"
            log.error("command %s failed: %s", name, exc)
        with session_scope(engine) as session:
            command = session.get(Command, command_id)
            command.status, command.result, command.processed_at = status, result[:4000], datetime.now(UTC)


def run_scheduler(settings: Settings, engine) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=ZoneInfo(settings.timezone))
    days = ",".join(settings.active_weekdays)
    scheduler.add_job(daily_cycle, CronTrigger(hour=settings.create_hour, minute=0, timezone=ZoneInfo(settings.timezone)),
                      args=[settings, engine], id="daily_cycle", max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
    scheduler.add_job(process_commands, "interval", minutes=1, args=[settings, engine], id="commands", max_instances=1, coalesce=True)
    log.info("scheduler started: daily cycle at %02d:00 %s (active: %s), mode=%s", settings.create_hour, settings.timezone, days, settings.mode.value)
    daily_cycle(settings, engine)  # catch up immediately on start
    scheduler.start()
