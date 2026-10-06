"""Animated scenes that repeat for hours: make the loop seamless once and cache it.

An AI video tool given the same picture as first and last frame (Kling's start/end frame)
comes close to a loop, but the motion still jumps where the clip restarts. The fix is to cut
the first FADE seconds off the front and fade them in over the end:

    clip:   [0 .. F][F ............................ L]
    loop:          [F ...................... L-F][fade: clip(L-F..L) -> clip(0..F)]

The loop is L-F seconds long and ends showing clip(F), exactly where it starts again, so
every repetition joins without a jump. The result is normalized to the output frame size and
frame rate and cached by the source's fingerprint: one short encode per scene, ever.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path

from app.visuals.indexer import fingerprint

log = logging.getLogger(__name__)

FADE_S = 0.75
CACHE_SUBDIR = "loops"


class LoopError(RuntimeError):
    pass


def loop_filter(duration: float, width: int, height: int, fps: int, fade: float = FADE_S) -> str:
    fade = min(fade, duration / 4)  # very short clips: a shorter fade, the loop keeps most of the clip
    return (
        f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,format=yuv420p,split[a][b];"
        f"[a]trim=start={fade:.3f}:end={duration:.3f},setpts=PTS-STARTPTS[body];"
        f"[b]trim=start=0:end={fade:.3f},setpts=PTS-STARTPTS[head];"
        f"[body][head]xfade=transition=fade:duration={fade:.3f}:offset={duration - 2 * fade:.3f}[v]"
    )


def prepared_loop(ffmpeg: Path, source: Path, duration: float, width: int, height: int, fps: int, cache_dir: Path) -> Path:
    """Seamless, normalized copy of `source` (cached); the original file is never modified."""
    out = cache_dir / CACHE_SUBDIR / f"{fingerprint(source)[:20]}_{width}x{height}_{fps}.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-filter_complex", loop_filter(duration, width, height, fps), "-map", "[v]", "-an",
        # An intermediate: it is encoded again with the grade and the vignette, so keep it near lossless.
        "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-g", str(fps), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(tmp),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise LoopError(f"could not prepare loop {source.name}: {result.stderr.strip()[-500:]}")
    tmp.replace(out)
    log.info("prepared seamless loop %s -> %s", source.name, out.name)
    return out


def with_prepared_loops(visuals: list, ffmpeg: Path, width: int, height: int, fps: int, cache_dir: Path) -> list:
    """The chosen visuals, with every loop pointing at its seamless cached copy."""
    return [
        replace(v, path=str(prepared_loop(ffmpeg, Path(v.path), float(v.duration or 0), width, height, fps, cache_dir)))
        if v.type == "loop" else v
        for v in visuals
    ]
