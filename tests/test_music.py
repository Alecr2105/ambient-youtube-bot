from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select

from app.audio.recipe import load_recipe
from app.database.migrate import upgrade_to_head
from app.database.models import Video
from app.database.session import make_engine, session_scope
from app.database.states import VideoState
from app.metadata.builder import build_description
from app.music import lofi
from app.quality.audio import AudioThresholds, check_audio
from app.utils import ffmpeg
from app.utils.config import GpuPolicy, Mode

FFMPEG = ffmpeg.resolve_binary("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")
SR = 48000


def fake_song(path: Path, seconds: float, seed: int, level: float = 0.1, sr: int = 44100) -> Path:
    """A stand-in for an ACE-Step track: a soft chord with a beat, at its own level."""
    t = np.arange(int(seconds * sr)) / sr
    rng = np.random.default_rng(seed)
    chord = sum(np.sin(2 * np.pi * f * t) for f in rng.choice([220, 247, 262, 294, 330, 349, 392], 3, replace=False))
    beat = 0.6 + 0.4 * (np.sin(2 * np.pi * 1.2 * t) > 0.9)
    mono = level * chord / 3 * beat + 0.003 * rng.standard_normal(len(t))
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.column_stack([mono, mono]), sr, subtype="PCM_16")
    return path


class FakeComfy:
    """Replaces the ComfyUI server: 'generates' each requested track at a random level."""

    calls: list[dict] = []

    def __init__(self, root, url):
        self.out = Path(root) / "out"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, graph, timeout=1800):
        FakeComfy.calls.append(graph)
        seed = graph["52"]["inputs"]["seed"]
        level = 0.03 + (seed % 7) * 0.02  # tracks come out at different loudness, like the real model
        return [fake_song(self.out / f"{len(FakeComfy.calls)}.flac", graph["17"]["inputs"]["seconds"], seed, level)]


# --- planning --------------------------------------------------------------

def test_playlist_covers_the_video_with_new_seeds_and_no_style_twice_in_a_row():
    plans = lofi.plan_tracks(12600, 180, 4, [], seed=3)
    assert len(plans) == lofi.tracks_needed(12600, 180, 4) == 72
    assert len(plans) * 180 - (len(plans) - 1) * 4 >= 12600
    assert all(a.style != b.style for a, b in zip(plans, plans[1:], strict=False))
    assert {p.style for p in plans} == set(lofi.STYLES) and len({p.seed for p in plans}) == len(plans)
    assert plans != lofi.plan_tracks(12600, 180, 4, [], seed=4)  # every video its own music
    with pytest.raises(lofi.MusicError):
        lofi.plan_tracks(600, 180, 4, ["polka"], seed=1)


def test_ace_step_graph_is_instrumental_and_uses_the_plan():
    plan = lofi.TrackPlan(0, "night_piano", 1234)
    graph = lofi.ace_step_graph(plan.tags, 180, plan.seed, "x")
    assert graph["14"]["inputs"]["lyrics"] == "[inst]" and "no vocals" in graph["14"]["inputs"]["tags"]
    assert "felt piano" in graph["14"]["inputs"]["tags"]
    assert graph["52"]["inputs"]["seed"] == 1234 and graph["17"]["inputs"]["seconds"] == 180


# --- mixing ----------------------------------------------------------------

@needs_ffmpeg
def test_mix_levels_the_tracks_keeps_the_rain_below_and_passes_the_music_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(lofi, "ComfyServer", FakeComfy)
    duration, track_s, xfade = 150.0, 60.0, 4.0
    bed = tmp_path / "bed.flac"
    rng = np.random.default_rng(0)
    sf.write(bed, rng.standard_normal((int(duration * SR), 2)) * 0.04, SR, subtype="PCM_24")  # steady 'rain'
    plans = lofi.plan_tracks(duration, track_s, xfade, [], seed=5)
    tracks, junctions = lofi.produce_music(FFMPEG, tmp_path, "http://x", plans, bed, 15.0, duration, track_s, xfade, -18.0, SR,
                                           tmp_path / "music", tmp_path / "audio.flac")

    assert len(tracks) == 3 and len({round(t.gain_db, 1) for t in tracks}) == 3  # each track levelled on its own
    assert junctions == [58.0, 114.0]
    thresholds = AudioThresholds(expected_duration_s=duration, max_short_term_jump_lu=8.0, loop_max_correlation=1.01)
    report = check_audio(tmp_path / "audio.flac", thresholds, junctions)
    assert report.passed, [f"{c.name}={c.value}" for c in report.failures()]
    assert sf.info(str(tmp_path / "audio.flac")).samplerate == SR

    # A resumed video does not generate its music again.
    calls = len(FakeComfy.calls)
    lofi.produce_music(FFMPEG, tmp_path, "http://x", plans, bed, 15.0, duration, track_s, xfade, -18.0, SR, tmp_path / "music", tmp_path / "again.flac")
    assert len(FakeComfy.calls) == calls


# --- description and selection ---------------------------------------------

def test_lofi_description_says_how_the_music_is_made():
    text = build_description(load_recipe("lofi_rain_window"), 210, True, "", "illustrated", "study")
    assert "AI music model" in text and "real rain" in text and "no music" not in text
    assert "no music" in build_description(load_recipe("rain_window_real"), 210, False, "")


# --- the whole pipeline ------------------------------------------------------

def own_recording(path: Path, seconds: float, seed: int) -> None:
    """Stand-in for a field recording: noise with slow level changes (as in test_pipeline)."""
    rng = np.random.default_rng(seed)
    steps = rng.uniform(0.5, 1.0, int(seconds) + 2)
    envelope = np.interp(np.arange(int(SR * seconds)) / SR, np.arange(len(steps)), steps)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, rng.standard_normal((int(SR * seconds), 2)) * 0.1 * envelope[:, None], SR, subtype="PCM_16")


@pytest.fixture
def lofi_env(make_settings, tmp_path, monkeypatch):
    from app.scheduler import pipeline
    from app.visuals.indexer import index_visuals

    visuals = tmp_path / "visuals"
    visuals.mkdir()
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=50", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(visuals / "rain_window_cabin.mp4")], check=True, timeout=120)
    own = tmp_path / "audio_own"
    for i, category in enumerate(("rain_window_texture", "light_rain_texture")):
        for n in range(2):
            own_recording(own / category / f"take{n}.wav", 65, seed=10 * i + n)
    settings = make_settings(
        visuals_dir=visuals, resolution="640x360", use_gpu=GpuPolicy.FALSE, video_bitrate="800k", video_duration=2,
        max_retries=0, backoff_base=0.01, min_free_disk_gb=0, audio_providers=["own"], audio_own_dir=own,
        music_mode=True, music_track_seconds=60, comfyui_dir=tmp_path / "comfy",
    )
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        index_visuals(session, visuals, ffmpeg.resolve_binary("ffprobe"))
    monkeypatch.setattr(pipeline, "research_recipe", lambda recipe, settings: [])
    monkeypatch.setattr(lofi, "ComfyServer", FakeComfy)
    yield settings, engine
    engine.dispose()


@needs_ffmpeg
def test_lofi_video_is_produced_with_music_rain_and_licenses(lofi_env):
    from app.scheduler.service import produce_for_day

    settings, engine = lofi_env
    video_id = produce_for_day(settings, engine, date(2026, 10, 6), "lofi_rain_window")
    assert video_id is not None
    out = settings.output_dir / video_id
    music = json.loads((out / "music.json").read_text(encoding="utf-8"))
    assert music["model"] == "ACE-Step v1 3.5B" and len(music["tracks"]) == 3
    licenses = json.loads((out / "licenses.json").read_text(encoding="utf-8"))
    kinds = [r["kind"] for r in licenses["resources"]]
    assert kinds.count("music") == 3 and licenses["all_valid"]
    assert all("ACE-Step" in r["provenance_notes"] for r in licenses["resources"] if r["kind"] == "music")
    checks = {c["name"]: c["passed"] for c in json.loads((out / "audio_report.json").read_text(encoding="utf-8"))["checks"]}
    assert checks["bed_loop_detection"] and checks["bed_calm_for_study"] and checks["integrated_loudness"]
    assert "AI music model" in json.loads((out / "metadata.json").read_text(encoding="utf-8"))["description"]


def test_music_mode_decides_the_format(make_settings, tmp_path):
    from app.scheduler.selector import choose_plan
    from app.visuals.matcher import NoMatchingVisualsError

    from app.database.models import Visual

    for music_mode in (True, False):
        settings = make_settings(music_mode=music_mode)
        upgrade_to_head(settings.database_url)
        engine = make_engine(settings.database_url)
        with session_scope(engine) as session:
            if session.scalar(select(Visual.id)) is None:
                session.add(Visual(path=str(tmp_path / "cabin.png"), type="image", width=1920, height=1080, tags=["rain", "window"], checksum="c"))
        picked = set()
        for seed in range(12):
            with session_scope(engine) as session:
                try:
                    picked.add(choose_plan(session, settings, date(2026, 10, 6), seed=seed).recipe.slug)
                except NoMatchingVisualsError:
                    pass
        engine.dispose()
        assert picked and all(slug.startswith("lofi_") == music_mode for slug in picked)


def test_test_videos_are_never_uploaded_and_never_fill_a_day(make_settings, monkeypatch):
    from app.scheduler import service

    settings = make_settings(mode=Mode.PRODUCTION)
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        session.add_all([
            Video(id="t", state=VideoState.READY, mode="test", seed=1, duration_seconds=60, target_publish_date=date(2026, 10, 6)),
            Video(id="p", state=VideoState.READY, mode="production", seed=2, duration_seconds=60, target_publish_date=date(2026, 10, 7)),
        ])
    uploaded = []
    monkeypatch.setattr(service, "upload_ready", lambda s, e, video_id: uploaded.append(video_id))
    service.upload_ready_videos(settings, engine)
    assert uploaded == ["p"]
    assert date(2026, 10, 6) in service.dates_needing_videos(settings, engine, date(2026, 10, 6))
    with session_scope(engine) as session:
        assert session.scalar(select(Video.mode).where(Video.id == "t")) == "test"
    engine.dispose()
