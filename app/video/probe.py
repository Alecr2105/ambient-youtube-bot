from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None
    width: int
    height: int
    fps: float | None
    codec: str | None
    has_audio: bool


def probe(ffprobe: Path, path: Path) -> MediaInfo:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    )
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"{path} has no video stream")
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"
    fps = float(Fraction(rate)) if rate not in ("0/0", "") else None
    duration = data.get("format", {}).get("duration") or video.get("duration")
    is_still = video.get("codec_name") in ("mjpeg", "png", "webp") and (duration is None or float(duration) < 0.1)
    return MediaInfo(
        duration=None if is_still or duration is None else float(duration),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=None if is_still else fps,
        codec=video.get("codec_name"),
        has_audio=any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    )
