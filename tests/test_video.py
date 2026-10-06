from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.database.migrate import upgrade_to_head
from app.database.models import Visual
from app.database.session import make_engine, session_scope
from app.quality.video import VideoThresholds, check_video
from app.utils import ffmpeg
from app.video.plan import STILL, is_still, plan_timeline, plan_variants
from app.visuals.indexer import index_visuals, tags_from_name
from app.visuals.matcher import NoMatchingVisualsError, VisualChoice, choose_visuals, score_visual

FFMPEG = ffmpeg.resolve_binary("ffmpeg")
FFPROBE = ffmpeg.resolve_binary("ffprobe")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="FFmpeg not installed")


def make_clip(path: Path, seconds: float, source: str = "testsrc2", size: str = "640x360") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=s={size}:r=30:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=120,
    )
    return path


def video_choice(path="a.mp4", duration=300.0, mirror=False) -> VisualChoice:
    return VisualChoice(1, path, "video", duration, 1920, 1080, mirror, ("rain",), 10.0)


# --- planning -------------------------------------------------------------

def test_variants_cover_their_length_inside_clip_bounds():
    variants = plan_variants([video_choice("a.mp4", 200), video_choice("b.mp4", 90)], 4, 120, 4, seed=3, sleep=False)
    for v in variants:
        total = sum(p.duration for p in v.pieces) - sum(v.inner_crossfades)
        assert total == pytest.approx(v.length, abs=0.01)
        assert len(v.inner_crossfades) == len(v.pieces) - 1
        for p in v.pieces:
            limit = 200 if p.path == "a.mp4" else 90
            assert 0 <= p.start and p.start + p.duration <= limit + 1e-6
            assert p.duration > 6.0


def test_variants_differ_by_seed():
    a = plan_variants([video_choice()], 2, 120, 4, seed=1, sleep=False)
    b = plan_variants([video_choice()], 2, 120, 4, seed=2, sleep=False)
    assert a != b and a == plan_variants([video_choice()], 2, 120, 4, seed=1, sleep=False)


def test_sleep_variants_get_darker():
    variants = plan_variants([video_choice()], 4, 120, 4, seed=5, sleep=True)
    plain = plan_variants([video_choice()], 4, 120, 4, seed=5, sleep=False)
    offsets = [s.grade.brightness - p.grade.brightness for s, p in zip(variants, plain, strict=True)]
    assert offsets == sorted(offsets, reverse=True) and offsets[-1] < -0.05


def test_timeline_covers_duration_without_back_to_back_repeats():
    timeline = plan_timeline(4 * 3600, 600, 4, 6, seed=9, sleep=False)
    assert timeline.total >= 4 * 3600
    assert timeline.total - 4 * 3600 < 604
    assert all(a != b for a, b in zip(timeline.order, timeline.order[1:], strict=False))
    assert set(timeline.order) == set(range(6))


def test_sleep_timeline_moves_forward_through_variants():
    order = plan_timeline(4 * 3600, 600, 4, 6, seed=9, sleep=True).order
    assert order == sorted(order) and order[0] == 0 and order[-1] == 5


def test_images_get_a_single_ken_burns_piece():
    image = VisualChoice(2, "sunset.jpg", "image", None, 4000, 3000, False, ("ocean",), 5.0)
    variant = plan_variants([image], 1, 60, 4, seed=1, sleep=False)[0]
    assert len(variant.pieces) == 1 and variant.pieces[0].is_image
    assert variant.motion.zoom_amplitude >= 0.04


def photo_choice(path: str) -> VisualChoice:
    return VisualChoice(abs(hash(path)) % 1000, path, "image", None, 1920, 1080, False, ("ocean",), 5.0)


def test_still_variants_have_no_motion_and_a_single_look():
    photos = [photo_choice(f"{n}.jpg") for n in "abc"]
    variants = plan_variants(photos, 4, 120, 4, seed=7, sleep=True, moving=False)
    assert is_still(variants) and all(v.motion == STILL for v in variants)
    # One grade for the whole video: no brightness step between segments, and no sleep ramp.
    assert len({v.grade for v in variants}) == 1
    assert not is_still(plan_variants(photos, 4, 120, 4, seed=7, sleep=True))


def test_footage_is_never_treated_as_a_still_picture():
    variants = plan_variants([video_choice()], 2, 120, 4, seed=7, sleep=False, moving=False)
    assert all(v.motion == STILL for v in variants) and not is_still(variants)


def test_each_segment_takes_a_different_photo_before_repeating_any():
    photos = [photo_choice(f"{n}.jpg") for n in "abcd"]
    used = [v.pieces[0].path for v in plan_variants(photos, 6, 120, 4, seed=11, sleep=False, moving=False)]
    assert len(set(used[:4])) == 4  # all four photos before any of them comes back
    assert max(used.count(path) for path in set(used)) == 2


# --- indexing and matching -------------------------------------------------

def test_tags_from_filename():
    assert tags_from_name(Path("Rain_on-Window 03 FINAL.mp4")) == ["rain", "window"]


@pytest.fixture
def engine(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


@needs_ffmpeg
def test_indexer_uses_yaml_tags_indexes_short_clips_as_loops_and_marks_missing(engine, tmp_path):
    root = tmp_path / "visuals"
    make_clip(root / "rain_window.mp4", 35)
    make_clip(root / "clip0001.mp4", 35)
    make_clip(root / "short_rain.mp4", 5)
    make_clip(root / "blink_rain.mp4", 1)
    make_clip(root / "long_rain_loop.mp4", 35)
    (root / "visuals.yaml").write_text(
        "files:\n  clip0001.mp4: {tags: [River, Jungle], allow_mirror: true}\n  long_rain_loop.mp4: {tags: [rain], loop: true}\n",
        encoding="utf-8",
    )
    with session_scope(engine) as session:
        report = index_visuals(session, root, FFPROBE)
    assert sorted(report.added) == ["clip0001.mp4", "long_rain_loop.mp4", "rain_window.mp4", "short_rain.mp4"]
    assert "blink_rain.mp4" in report.skipped  # too short to loop
    with session_scope(engine) as session:
        kinds = {Path(v.path).name: v.type for v in session.query(Visual)}
        assert kinds == {"clip0001.mp4": "video", "long_rain_loop.mp4": "loop", "rain_window.mp4": "video", "short_rain.mp4": "loop"}
        river = next(v for v in session.query(Visual) if v.path.endswith("clip0001.mp4"))
        assert river.tags == ["jungle", "river"] and river.allow_mirror and river.duration == pytest.approx(35, abs=0.2)
    (root / "rain_window.mp4").unlink()
    with session_scope(engine) as session:
        report = index_visuals(session, root, FFPROBE)
    assert [Path(p).name for p in report.missing] == ["rain_window.mp4"]


def test_matcher_prefers_tag_overlap_and_rests_recent_clips(engine):
    now = datetime(2026, 9, 14, tzinfo=UTC)
    with session_scope(engine) as session:
        session.add_all([
            Visual(path="/a.mp4", type="video", duration=600, width=1920, height=1080, fps=30, tags=["rain", "window"], checksum="a"),
            Visual(path="/b.mp4", type="video", duration=600, width=1920, height=1080, fps=30, tags=["rain", "window"], checksum="b", last_used_at=now - timedelta(days=1), usage_count=3),
            Visual(path="/c.mp4", type="video", duration=600, width=1920, height=1080, fps=30, tags=["fire"], checksum="c"),
        ])
    with session_scope(engine) as session:
        picked = choose_visuals(session, ["rain", "window"], now=now)
        assert [p.path for p in picked] == ["/a.mp4", "/b.mp4"]
        with pytest.raises(NoMatchingVisualsError):
            choose_visuals(session, ["ocean"], now=now)
        assert score_visual(session.query(Visual).filter_by(path="/c.mp4").one(), {"rain"}, now) == 0


# --- rendering (integration, small resolution) -----------------------------

@needs_ffmpeg
def test_render_builds_exact_length_video_without_visible_junctions(make_settings, tmp_path):
    from app.utils.config import GpuPolicy
    from app.video.render import VideoFormat, render_video

    settings = make_settings(resolution="640x360", use_gpu=GpuPolicy.FALSE, video_bitrate="1M")
    clip = make_clip(tmp_path / "src" / "forest.mp4", 50)
    audio = tmp_path / "audio.flac"
    sf.write(audio, np.random.default_rng(0).standard_normal((48000 * 75, 2)) * 0.05, 48000)
    visuals = [VisualChoice(1, str(clip), "video", 50.0, 640, 360, True, ("forest",), 10.0)]
    variants = plan_variants(visuals, 2, 20, 3, seed=4, sleep=False)
    timeline = plan_timeline(75, 20, 3, 2, seed=4, sleep=False)
    encoder = ffmpeg.select_video_encoder("h264", GpuPolicy.FALSE, ffmpeg.list_encoders(FFMPEG), lambda n: False)
    fmt = VideoFormat(640, 360, 30, "1M", "aac", "128k")
    result = render_video(FFMPEG, FFPROBE, variants, timeline, audio, 75.0, fmt, encoder, tmp_path / "work", tmp_path / "out" / "video.mp4", tmp_path / "cache")

    assert not (tmp_path / "work").exists()
    assert len(result.junctions_s) >= 4
    report = check_video(FFMPEG, FFPROBE, result.path, VideoThresholds(75, 640, 360, 30), result.junctions_s)
    assert report.passed, [f"{c.name}={c.value} {c.detail}" for c in report.failures()]


@needs_ffmpeg
def test_hard_cut_is_detected_as_junction_jump(tmp_path):
    a = make_clip(tmp_path / "a.mp4", 6, "testsrc2")
    b = make_clip(tmp_path / "b.mp4", 6, "smptebars")
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{a.as_posix()}'\nfile '{b.as_posix()}'\n", encoding="utf-8")
    cut = tmp_path / "cut.mp4"
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-t", "12", str(cut)],
        check=True, timeout=120,
    )
    report = check_video(FFMPEG, FFPROBE, cut, VideoThresholds(12, 640, 360, 30, ignore_edges_s=0.5), [6.0])
    assert not next(c for c in report.checks if c.name == "junction_jumps").passed
