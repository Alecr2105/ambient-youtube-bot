"""Thumbnails built only from frames of the video itself, with large English text."""

from __future__ import annotations

import io
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.audio.recipe import Recipe
from app.metadata.language import is_english_only
from app.metadata.titles import duration_label

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024  # YouTube thumbnail limit
FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Montserrat-Variable.ttf"
FILLER = {"a", "an", "the", "on", "in", "of", "and", "with", "for"}


@dataclass(frozen=True)
class FrameCandidate:
    time: float
    image: Image.Image
    sharpness: float
    exposure: float
    contrast: float
    score: float


@dataclass(frozen=True)
class Thumbnail:
    path: Path
    frame_time: float
    lines: tuple[str, ...]
    layout: str
    score: float


def extract_frame(ffmpeg: Path, video: Path, at: float) -> Image.Image | None:
    result = subprocess.run(
        [str(ffmpeg), "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={WIDTH}:{HEIGHT}", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return Image.open(io.BytesIO(result.stdout)).convert("RGB")


def frame_quality(image: Image.Image) -> tuple[float, float, float, float]:
    gray = np.asarray(image.convert("L").resize((320, 180)), dtype=np.float32) / 255
    laplacian = gray[1:-1, 1:-1] * 4 - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    sharpness = float(laplacian.var() * 1000)
    exposure = float(gray.mean())
    contrast = float(gray.std())
    clipped = float(((gray < 0.02) | (gray > 0.98)).mean())
    exposure_score = 1 - min(abs(exposure - 0.45) / 0.35, 1)
    score = min(sharpness / 4, 1.0) * 2 + exposure_score * 1.5 + min(contrast / 0.22, 1.0) - clipped * 3
    return sharpness, exposure, contrast, score


def pick_frames(ffmpeg: Path, video: Path, duration: float, junctions: list[float], count: int = 24, keep: int = 5) -> list[FrameCandidate]:
    start, end = min(60.0, duration * 0.1), duration - min(60.0, duration * 0.1)
    times = [t for t in np.linspace(start, end, count) if all(abs(t - j) > 5 for j in junctions)]
    candidates = []
    for t in times:
        image = extract_frame(ffmpeg, video, float(t))
        if image is None:
            continue
        sharpness, exposure, contrast, score = frame_quality(image)
        candidates.append(FrameCandidate(float(t), image, sharpness, exposure, contrast, score))
    candidates.sort(key=lambda c: -c.score)
    chosen: list[FrameCandidate] = []
    min_gap = duration / (keep * 2)
    for c in candidates:
        if all(abs(c.time - o.time) >= min_gap for o in chosen):
            chosen.append(c)
        if len(chosen) == keep:
            break
    return chosen


def text_options(recipe: Recipe, minutes: float) -> list[tuple[str, ...]]:
    words = [w for w in re.findall(r"[A-Za-z]+", recipe.name) if w.lower() not in FILLER]
    subject = " ".join(words[:2]).upper() if len(words) >= 2 else recipe.name.upper()
    purpose = {"sleep": "DEEP SLEEP", "study": "FOCUS", "relaxation": "RELAX"}[recipe.subniches[0]]
    hours = duration_label(minutes).upper()
    options = [(subject, hours), (subject, purpose), (subject,)]
    return [o for o in options if 2 <= sum(len(line.split()) for line in o) <= 4 or len(o) == 1]


def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    try:
        font.set_variation_by_axes([800])
    except OSError:
        pass
    return font


def compose(frame: Image.Image, lines: tuple[str, ...], layout: str) -> tuple[Image.Image, float]:
    image = ImageEnhance.Contrast(frame).enhance(1.06)
    image = ImageEnhance.Color(image).enhance(1.08)
    draw_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)
    sizes = [150 if i == 0 else 96 for i in range(len(lines))]
    fonts = [_font(s) for s in sizes]
    boxes = [draw.textbbox((0, 0), line, font=f) for line, f in zip(lines, fonts, strict=True)]
    max_width = max(b[2] - b[0] for b in boxes)
    scale = min(1.0, (WIDTH * 0.86) / max_width)
    if scale < 1.0:
        fonts = [_font(int(s * scale)) for s in sizes]
        boxes = [draw.textbbox((0, 0), line, font=f) for line, f in zip(lines, fonts, strict=True)]
    heights = [b[3] - b[1] for b in boxes]
    block_h = sum(heights) + 18 * (len(lines) - 1)
    margin = 60
    if layout == "bottom-left":
        x0, y0, anchor_x = margin, HEIGHT - margin - block_h, "left"
    elif layout == "top-left":
        x0, y0, anchor_x = margin, margin, "left"
    else:
        x0, y0, anchor_x = WIDTH // 2, (HEIGHT - block_h) // 2, "center"

    # Soft dark band behind the text for legibility on any footage.
    band = Image.new("L", image.size, 0)
    band_draw = ImageDraw.Draw(band)
    band_draw.rectangle([0, max(y0 - 50, 0), WIDTH, min(y0 + block_h + 50, HEIGHT)], fill=150)
    band = band.filter(ImageFilter.GaussianBlur(40))
    shade = Image.new("RGBA", image.size, (0, 0, 0, 255))
    shade.putalpha(band)
    image = Image.alpha_composite(image.convert("RGBA"), shade)

    y = y0
    region_luma = []
    for line, font, box, h in zip(lines, fonts, boxes, heights, strict=True):
        w = box[2] - box[0]
        x = x0 - w // 2 if anchor_x == "center" else x0
        area = np.asarray(image.convert("L").crop((max(x, 0), max(y, 0), min(x + w, WIDTH), min(y + h + box[1], HEIGHT))), dtype=np.float32)
        region_luma.append(area.mean() / 255 if area.size else 0.5)
        draw.text((x + 4, y - box[1] + 5), line, font=font, fill=(0, 0, 0, 170))
        draw.text((x, y - box[1]), line, font=font, fill=(255, 255, 255, 255))
        y += h + 18
    image = Image.alpha_composite(image, draw_layer).convert("RGB")
    legibility = 1 - float(np.mean(region_luma))  # white text reads best over darker regions
    return image, legibility


def save_jpeg(image: Image.Image, path: Path) -> None:
    for quality in (92, 85, 78, 70):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= MAX_BYTES:
            path.write_bytes(buffer.getvalue())
            return
    raise ValueError("thumbnail cannot be compressed under 2 MB")


def generate_thumbnails(ffmpeg: Path, video: Path, duration: float, junctions: list[float], recipe: Recipe, minutes: float, out_dir: Path, variants: int = 5) -> tuple[Thumbnail, list[Thumbnail]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = pick_frames(ffmpeg, video, duration, junctions, keep=variants)
    if not frames:
        raise ValueError("no usable frame for thumbnails")
    options = text_options(recipe, minutes)
    layouts = ["bottom-left", "center", "top-left"]
    thumbs = []
    for i in range(variants):
        frame = frames[i % len(frames)]
        lines = options[i % len(options)]
        if not all(is_english_only(line) for line in lines):
            continue
        layout = layouts[i % len(layouts)]
        image, legibility = compose(frame.image, lines, layout)
        path = out_dir / f"thumb_{i + 1}.jpg"
        save_jpeg(image, path)
        score = round(frame.score + legibility * 2 + (0.3 if len(lines) == 2 else 0), 3)
        thumbs.append(Thumbnail(path, frame.time, lines, layout, score))
    best = max(thumbs, key=lambda t: t.score)
    selected = out_dir.parent / "thumb_selected.jpg"
    selected.write_bytes(best.path.read_bytes())
    return Thumbnail(selected, best.frame_time, best.lines, best.layout, best.score), thumbs
