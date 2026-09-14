from __future__ import annotations

import subprocess

import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.audio.recipe import all_recipes, load_recipe
from app.metadata.language import is_english_only
from app.thumbnails.generator import FONT_PATH, HEIGHT, MAX_BYTES, WIDTH, compose, frame_quality, generate_thumbnails, text_options
from app.utils import ffmpeg

FFMPEG = ffmpeg.resolve_binary("ffmpeg")


def test_font_and_license_are_bundled():
    assert FONT_PATH.exists()
    assert "SIL Open Font License" in (FONT_PATH.parent / "OFL.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("recipe", all_recipes(), ids=lambda r: r.slug)
def test_text_options_are_short_english(recipe):
    for lines in text_options(recipe, 240):
        words = sum(len(line.split()) for line in lines)
        assert 1 <= words <= 4
        assert all(is_english_only(line) and line == line.upper() for line in lines)


def test_quality_prefers_sharp_well_exposed_frames():
    rng = np.random.default_rng(0)
    detailed = Image.fromarray((rng.random((720, 1280, 3)) * 180 + 40).astype(np.uint8))
    blurred = detailed.filter(ImageFilter.GaussianBlur(12))
    dark = Image.fromarray(np.full((720, 1280, 3), 3, np.uint8))
    assert frame_quality(detailed)[3] > frame_quality(blurred)[3] > frame_quality(dark)[3]


def test_compose_keeps_size_and_draws_text():
    base = Image.new("RGB", (WIDTH, HEIGHT), (40, 90, 60))
    image, legibility = compose(base, ("HEAVY RAIN", "4 HOURS"), "bottom-left")
    assert image.size == (WIDTH, HEIGHT)
    assert np.asarray(image).max() >= 250  # white text present
    assert 0 <= legibility <= 1


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")
def test_generate_thumbnails_from_video(tmp_path):
    video = tmp_path / "video.mp4"
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=150",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
        check=True, timeout=120,
    )
    selected, thumbs = generate_thumbnails(FFMPEG, video, 150, [75.0], load_recipe("heavy_rain_window"), 240, tmp_path / "pkg" / "thumbs")
    assert len(thumbs) == 5
    assert selected.path == tmp_path / "pkg" / "thumb_selected.jpg" and selected.path.exists()
    for thumb in thumbs:
        assert thumb.path.stat().st_size <= MAX_BYTES
        assert Image.open(thumb.path).size == (WIDTH, HEIGHT)
        assert abs(thumb.frame_time - 75.0) > 5
    assert len({t.layout for t in thumbs}) >= 2
