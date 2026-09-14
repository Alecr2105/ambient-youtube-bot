"""Builds and uploads a short PRIVATE test video to verify the API integration end to end."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from app.database.session import make_engine
from app.utils import ffmpeg
from app.utils.config import Settings
from app.youtube.auth import build_service, load_credentials
from app.youtube.quota import QuotaTracker
from app.youtube.uploader import VideoMetadata, get_video, upload_video

log = logging.getLogger(__name__)

TEST_SECONDS = 30


def render_test_video(settings: Settings, out_path: Path) -> Path:
    binary = ffmpeg.require_binary("ffmpeg", settings.ffmpeg_path)
    encoder = ffmpeg.select_video_encoder(
        settings.video_codec, settings.use_gpu, ffmpeg.list_encoders(binary), lambda name: ffmpeg.encoder_works(binary, name)
    )
    previews = sorted((settings.output_dir / "previews").glob("gentle_rain*.flac")) or sorted((settings.output_dir / "previews").glob("*.flac"))
    audio_input = ["-i", str(previews[0])] if previews else ["-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.05:d={TEST_SECONDS}"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"gradients=s={settings.resolution}:r={settings.fps}:d={TEST_SECONDS}:speed=0.005:c0=0x16302a:c1=0x2f6b4f",
        *audio_input,
        "-t", str(TEST_SECONDS), "-map", "0:v", "-map", "1:a",
        "-c:v", encoder.name, "-b:v", "4M", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-af", f"afade=t=in:d=2,afade=t=out:st={TEST_SECONDS - 3}:d=3",
        "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(command, check=True, timeout=300)
    return out_path


def run_test_upload(settings: Settings) -> dict:
    run_dir = settings.output_dir / "youtube_tests" / datetime.now().strftime("%Y%m%d_%H%M%S")
    video = render_test_video(settings, run_dir / "test.mp4")
    credentials = load_credentials(settings, interactive=False)
    service = build_service(credentials)
    engine = make_engine(settings.database_url)
    quota = QuotaTracker(engine, settings)
    meta = VideoMetadata(
        title="Private API test upload - please ignore",
        description="Private test upload made by Ambient Bot to verify its YouTube API integration. Not for publication.",
        tags=["test"],
        category_id=settings.youtube_category_id,
        privacy_status="private",
        contains_synthetic_media=settings.declare_synthetic_media,
    )
    response = upload_video(service, video, meta, quota, progress=lambda p: log.info("upload %.0f%%", p * 100))
    details = get_video(service, response["id"], quota)
    result = {
        "youtube_video_id": response["id"],
        "studio_url": f"https://studio.youtube.com/video/{response['id']}/edit",
        "privacy_status": (details or response).get("status", {}).get("privacyStatus"),
        "made_for_kids": (details or response).get("status", {}).get("madeForKids"),
        "self_declared_made_for_kids": (details or response).get("status", {}).get("selfDeclaredMadeForKids"),
        "quota_used_today": {bucket: quota.used(bucket) for bucket in ("uploads", "general")},
    }
    (run_dir / "result.json").write_text(json.dumps({"result": result, "response": response}, indent=2), encoding="utf-8")
    engine.dispose()
    return result
