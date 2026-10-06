from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.database.migrate import upgrade_to_head
from app.database.models import Visual
from app.database.session import make_engine, session_scope
from app.quality.video import VideoThresholds, check_video
from app.stream.sync import animated_scene
from app.utils import ffmpeg
from app.video.plan import is_animated, is_still, plan_timeline, plan_variants
from app.visuals.loops import FADE_S, prepared_loop, with_prepared_loops
from app.visuals.matcher import VisualChoice, choose_visuals

FFMPEG = ffmpeg.resolve_binary("ffmpeg")
FFPROBE = ffmpeg.resolve_binary("ffprobe")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="FFmpeg not installed")
SIZE = (160, 90)


def make_clip(path: Path, seconds: float) -> Path:
    """A box sliding slowly across the frame: small steps between frames, but the last frame is
    far from the first, like an AI clip whose motion does not come back to where it started."""
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (f"color=c=0x203040:s=640x360:r=24:d={seconds}[bg];color=c=0xE0C080:s=100x100:r=24:d={seconds}[box];"
              "[bg][box]overlay=x='20+t*60':y=130")
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120)
    return path


def frames(path: Path) -> np.ndarray:
    raw = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(path), "-vf", f"scale={SIZE[0]}:{SIZE[1]},format=gray",
                          "-f", "rawvideo", "-"], check=True, capture_output=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, SIZE[1], SIZE[0]).astype(np.float32)


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def loop_choice(path: str, duration: float = 6.0, n: int = 1) -> VisualChoice:
    return VisualChoice(n, path, "loop", duration, 1920, 1080, False, ("rain",), 10.0)


# --- seamless loop ---------------------------------------------------------

@needs_ffmpeg
def test_prepared_loop_restarts_without_a_jump_and_is_cached(tmp_path):
    source = make_clip(tmp_path / "kling_rain.mp4", 6)
    original = source.read_bytes()
    loop = prepared_loop(FFMPEG, source, 6.0, 640, 360, 30, tmp_path / "cache")

    raw, looped = frames(source), frames(loop)
    def typical_step(f: np.ndarray) -> float:
        return float(np.median([diff(a, b) for a, b in zip(f, f[1:], strict=False)]))

    # What the viewer sees when the loop starts again: no bigger than an ordinary frame step...
    assert diff(looped[-1], looped[0]) <= 1.5 * typical_step(looped)
    # ...where the clip as delivered jumps by several steps at once.
    assert diff(raw[-1], raw[0]) > 3 * typical_step(raw)
    assert len(looped) / 30 == pytest.approx(6.0 - FADE_S, abs=0.1)  # normalized to 30 fps
    assert source.read_bytes() == original  # the owner's file is never touched

    stamp = loop.stat().st_mtime_ns
    assert prepared_loop(FFMPEG, source, 6.0, 640, 360, 30, tmp_path / "cache") == loop
    assert loop.stat().st_mtime_ns == stamp  # cached: encoded once per scene


@needs_ffmpeg
def test_only_loops_are_swapped_for_their_prepared_copy(tmp_path):
    source = make_clip(tmp_path / "rain.mp4", 4)
    photo = VisualChoice(2, str(tmp_path / "rain.jpg"), "image", None, 1920, 1080, False, ("rain",), 5.0)
    swapped = with_prepared_loops([loop_choice(str(source), 4.0), photo], FFMPEG, 640, 360, 30, tmp_path / "cache")
    assert Path(swapped[0].path).parent == tmp_path / "cache" / "loops" and swapped[0].id == 1
    assert swapped[1] == photo


# --- choosing and planning -------------------------------------------------

def test_matcher_prefers_animated_scenes_and_never_mixes_them_with_photos(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        session.add_all([
            Visual(path="/cabin.png", type="image", width=1920, height=1080, tags=["rain", "window"], checksum="a"),
            Visual(path="/cabin_loop.mp4", type="loop", duration=10, width=1920, height=1080, fps=24, tags=["rain"], checksum="b"),
            Visual(path="/roof_loop.mp4", type="loop", duration=10, width=1920, height=1080, fps=24, tags=["rain", "roof"], checksum="c"),
        ])
    with session_scope(engine) as session:
        assert [v.path for v in choose_visuals(session, ["rain", "window", "roof"])] == ["/roof_loop.mp4", "/cabin_loop.mp4"]
        assert [v.path for v in choose_visuals(session, ["window"])] == ["/cabin.png"]  # no loop for this scene yet
    engine.dispose()


def test_loops_fill_one_segment_each_and_are_animated_not_still():
    loops = [loop_choice(f"{n}.mp4", n=i) for i, n in enumerate("abc")]
    variants = plan_variants(loops, 6, 120, 4, seed=3, sleep=False, moving=False)
    pieces = [v.pieces for v in variants]
    assert all(len(p) == 1 and p[0].loop and not p[0].is_image and p[0].duration == 128 for p in pieces)
    assert len({p[0].path for p in pieces[:3]}) == 3  # every scene before any repeats
    assert is_animated(variants) and not is_still(variants)


# --- rendering -------------------------------------------------------------

@needs_ffmpeg
def test_render_with_an_animated_loop_passes_the_video_gate(make_settings, tmp_path):
    from app.utils.config import GpuPolicy
    from app.video.render import VideoFormat, render_video

    loop = prepared_loop(FFMPEG, make_clip(tmp_path / "src" / "rain.mp4", 5), 5.0, 640, 360, 30, tmp_path / "cache")
    audio = tmp_path / "audio.flac"
    sf.write(audio, np.random.default_rng(0).standard_normal((48000 * 60, 2)) * 0.05, 48000)
    variants = plan_variants([loop_choice(str(loop), 4.25)], 2, 20, 3, seed=4, sleep=False, moving=False)
    timeline = plan_timeline(60, 20, 3, 2, seed=4, sleep=False)
    encoder = ffmpeg.select_video_encoder("h264", GpuPolicy.FALSE, ffmpeg.list_encoders(FFMPEG), lambda n: False)
    fmt = VideoFormat(640, 360, 30, "1M", "aac", "128k")
    result = render_video(FFMPEG, FFPROBE, variants, timeline, audio, 60.0, fmt, encoder, tmp_path / "work", tmp_path / "out" / "video.mp4", tmp_path / "cache")

    report = check_video(FFMPEG, FFPROBE, result.path, VideoThresholds(60, 640, 360, 30), result.junctions_s)
    assert report.passed, [f"{c.name}={c.value} {c.detail}" for c in report.failures()]
    # The scene keeps moving the whole time: no frozen stretch where the loop ran out.
    moving = frames(result.path)[::30]
    assert min(diff(a, b) for a, b in zip(moving[3:-3], moving[4:-3], strict=False)) > 0.5


# --- live stream -----------------------------------------------------------

def test_live_stream_uses_the_loop_the_video_was_made_from(tmp_path):
    loop = tmp_path / "cache" / "loops" / "abc_1920x1080_30.mp4"
    loop.parent.mkdir(parents=True)
    loop.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    plan = {"variants": [{"pieces": [{"path": str(loop), "loop": True}]}]}
    (out / "visual_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert animated_scene(out) == loop
    (out / "visual_plan.json").write_text(json.dumps({"variants": [{"pieces": [{"path": "/p.jpg", "is_image": True}]}]}), encoding="utf-8")
    assert animated_scene(out) is None  # a still picture: the stream takes a frame of the video
