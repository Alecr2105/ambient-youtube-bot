from __future__ import annotations

import subprocess
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.database.migrate import upgrade_to_head
from app.database.models import ErrorLog, StateLog, Title, Video, Visual
from app.database.session import make_engine, session_scope
from app.database.states import VideoState
from app.scheduler import pipeline
from app.scheduler.selector import choose_plan
from app.scheduler.service import dates_needing_videos, new_video, produce_for_day
from app.utils import ffmpeg
from app.utils.config import GpuPolicy

FFMPEG = ffmpeg.resolve_binary("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")
SR = 48000


def own_recording(path: Path, seconds: float, seed: int) -> None:
    """Stand-in for a field recording in assets/audio_own/<category>/: noise with slow level changes."""
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(seed)
    steps = rng.uniform(0.5, 1.0, int(seconds) + 2)
    envelope = np.interp(np.arange(int(SR * seconds)) / SR, np.arange(len(steps)), steps)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, rng.standard_normal((int(SR * seconds), 2)) * 0.1 * envelope[:, None], SR, subtype="PCM_16")


@pytest.fixture
def env(make_settings, tmp_path, monkeypatch):
    visuals = tmp_path / "visuals"
    visuals.mkdir()
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=50",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(visuals / "rain_window_forest.mp4")],
        check=True, timeout=120,
    )
    # Every sound is a real recording: give heavy_rain_window's two layers something to use.
    own = tmp_path / "audio_own"
    for i, category in enumerate(("rain_window_texture", "heavy_rain_texture")):
        for n in range(2):
            own_recording(own / category / f"take{n}.wav", 65, seed=10 * i + n)
    settings = make_settings(
        visuals_dir=visuals, resolution="640x360", use_gpu=GpuPolicy.FALSE, video_bitrate="800k",
        video_duration=1, max_retries=1, backoff_base=0.01, min_free_disk_gb=0, audio_providers=["own"],
        audio_own_dir=own,
    )
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    from app.visuals.indexer import index_visuals

    with session_scope(engine) as session:
        index_visuals(session, visuals, ffmpeg.resolve_binary("ffprobe"))
    monkeypatch.setattr(pipeline, "research_recipe", lambda recipe, settings: [])
    yield settings, engine
    engine.dispose()


def states(engine, video_id):
    with session_scope(engine) as session:
        return [row.to_state for row in session.scalars(select(StateLog).where(StateLog.video_id == video_id).order_by(StateLog.id))]


def test_full_pipeline_produces_complete_package(env):
    settings, engine = env
    video_id = produce_for_day(settings, engine, date(2026, 9, 20), "heavy_rain_window")
    assert video_id is not None
    out = settings.output_dir / video_id
    for name in ("video.mp4", "audio.flac", "thumb_selected.jpg", "metadata.json", "licenses.json", "quality_report.json"):
        assert (out / name).exists(), name
    assert len(list((out / "thumbs").glob("thumb_*.jpg"))) == 5
    assert states(engine, video_id) == list(pipeline.PIPELINE_ORDER[1:10])
    assert not (settings.work_dir / video_id).exists()
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        assert video.state is VideoState.READY and video.combination_hash
        assert session.scalar(select(Title.text).where(Title.video_id == video_id))
        assert session.scalar(select(Visual.usage_count)) == 1

    # Same recipe and footage three days later: rejected at the very first stage, nothing rendered.
    assert produce_for_day(settings, engine, date(2026, 9, 23), "heavy_rain_window") is None
    with session_scope(engine) as session:
        blocked = session.scalars(select(Video).where(Video.target_publish_date == date(2026, 9, 23))).one()
        assert blocked.failed_from_state is VideoState.RESEARCHING
    assert not (settings.output_dir / blocked.id / "audio.flac").exists()

    second = produce_for_day(settings, engine, date(2026, 9, 28), "heavy_rain_window")
    assert second and second != video_id
    import json

    first_meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    second_meta = json.loads((settings.output_dir / second / "metadata.json").read_text(encoding="utf-8"))
    assert first_meta["title"] != second_meta["title"]
    assert (out / "audio.flac").read_bytes() != (settings.output_dir / second / "audio.flac").read_bytes()


def test_failure_is_retried_then_resumed_without_redoing_finished_stages(env, monkeypatch):
    settings, engine = env
    video_id = new_video(settings, engine, date(2026, 9, 21), "heavy_rain_window")
    real_render = pipeline.STAGES[VideoState.RENDERING]
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        raise OSError("disk hiccup")

    monkeypatch.setitem(pipeline.STAGES, VideoState.RENDERING, flaky)
    assert pipeline.run_production(settings, engine, video_id, sleep=lambda s: None) is VideoState.FAILED
    assert calls["n"] == settings.max_retries + 1
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        assert video.failed_from_state is VideoState.RENDERING
        assert session.scalar(select(ErrorLog.stage).where(ErrorLog.video_id == video_id)) == "RENDERING"

    audio = settings.output_dir / video_id / "audio.flac"
    stamp = audio.stat().st_mtime_ns
    monkeypatch.setitem(pipeline.STAGES, VideoState.RENDERING, real_render)
    assert pipeline.run_production(settings, engine, video_id) is VideoState.READY
    assert audio.stat().st_mtime_ns == stamp  # audio was not rendered again


def test_content_errors_fail_without_retrying(env, monkeypatch):
    settings, engine = env
    video_id = new_video(settings, engine, date(2026, 9, 22), "heavy_rain_window")
    calls = {"n": 0}

    def no_footage(ctx):
        calls["n"] += 1
        raise pipeline.NoMatchingVisualsError("gone")

    monkeypatch.setitem(pipeline.STAGES, VideoState.VISUAL_SELECTION, no_footage)
    assert pipeline.run_production(settings, engine, video_id, sleep=lambda s: None) is VideoState.FAILED
    assert calls["n"] == 1


def test_selector_respects_duration_override_window_and_variety(env):
    settings, engine = env
    with session_scope(engine) as session:
        plan = choose_plan(session, settings, date(2026, 9, 23), seed=5)
    assert plan.duration_seconds == 60
    local = plan.publish_at.astimezone(ZoneInfo("America/New_York"))
    assert time(20, 0) <= local.time() <= time(22, 0)
    new_video(settings, engine, date(2026, 9, 23), plan.recipe.slug)
    with session_scope(engine) as session:
        nxt = choose_plan(session, settings, date(2026, 9, 24), seed=5)
    assert nxt.recipe.slug != plan.recipe.slug


def test_buffer_counts_existing_videos(env):
    settings, engine = env
    today = date(2026, 9, 14)
    assert dates_needing_videos(settings, engine, today) == [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)]
    new_video(settings, engine, date(2026, 9, 15), "heavy_rain_window")
    assert dates_needing_videos(settings, engine, today) == [date(2026, 9, 14), date(2026, 9, 16)]


def test_upload_schedules_ahead_and_publishes_at_once_when_the_slot_has_passed(env, monkeypatch):
    """A video finished after its publish time must go live, not sit private forever."""
    from datetime import UTC, datetime, timedelta

    import app.youtube.auth as yt_auth
    import app.youtube.uploader as yt_uploader
    from app.scheduler.service import upload_ready
    from app.utils.config import Mode

    settings, engine = env
    settings = settings.model_copy(update={"mode": Mode.PRODUCTION})
    video_id = produce_for_day(settings, engine, date(2026, 9, 20), "heavy_rain_window")
    sent = []

    def fake_upload(service, path, meta, quota, **kw):
        sent.append((meta.privacy_status, meta.publish_at))
        return {"id": f"yt{len(sent)}", "status": {"privacyStatus": meta.privacy_status}}

    monkeypatch.setattr(yt_auth, "load_credentials", lambda s: object())
    monkeypatch.setattr(yt_auth, "build_service", lambda c: object())
    monkeypatch.setattr(yt_uploader, "upload_video", fake_upload)
    monkeypatch.setattr(yt_uploader, "set_thumbnail", lambda *a, **k: None)

    for publish_at in (datetime.now(UTC) + timedelta(days=1), datetime.now(UTC) - timedelta(hours=1)):
        with session_scope(engine) as session:
            video = session.get(Video, video_id)
            video.state, video.publish_at = VideoState.READY, publish_at
        upload_ready(settings, engine, video_id)

    (ahead_privacy, ahead_at), (late_privacy, late_at) = sent
    assert ahead_privacy == "private" and ahead_at is not None  # YouTube makes it public at publishAt
    assert late_privacy == "public" and late_at is None
