"""FFmpeg rendering: segment variants, edge transitions and stream-copy assembly.

Each variant is rendered once as head(edge) + body + tail(edge) with forced keyframes at the
split points, then cut with stream copy. The final video is
    head(v1) body(v1) T(v1,v2) body(v2) ... body(vn) tail(vn)
where T is a crossfade from one variant's tail into the next one's head, encoded with the
same settings, so the whole timeline concatenates without re-encoding.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.utils.ffmpeg import EncoderChoice
from app.video.plan import SegmentVariant, Timeline
from app.video.probe import probe

log = logging.getLogger(__name__)

FADE_IN_S = 3.0


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoFormat:
    width: int
    height: int
    fps: int
    bitrate: str
    audio_codec: str
    audio_bitrate: str


@dataclass
class VideoRenderResult:
    path: Path
    duration: float
    junctions_s: list[float]
    timings: dict[str, float] = field(default_factory=dict)


def _bitrate_bps(value: str) -> int:
    value = value.strip().upper()
    scale = {"K": 1_000, "M": 1_000_000}.get(value[-1], 1)
    return int(float(value[:-1] if value[-1] in "KM" else value) * scale)


def encode_args(encoder: EncoderChoice, fmt: VideoFormat, key_times: list[float]) -> list[str]:
    bps = _bitrate_bps(fmt.bitrate)
    common = ["-pix_fmt", "yuv420p", "-r", str(fmt.fps), "-g", str(fmt.fps * 2), "-bf", "0"]
    if key_times:
        common += ["-force_key_frames", ",".join(f"{t:.3f}" for t in key_times)]
    rate = ["-b:v", str(bps), "-maxrate", str(int(bps * 1.5)), "-bufsize", str(bps * 2)]
    if encoder.name.endswith("_nvenc"):
        return ["-c:v", encoder.name, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-forced-idr", "1", *rate, *common]
    if encoder.name in ("libx264", "libx265"):
        params = "scenecut=0:open-gop=0" if encoder.name == "libx264" else "scenecut=0:open-gop=0:bframes=0"
        flag = "-x264-params" if encoder.name == "libx264" else "-x265-params"
        return ["-c:v", encoder.name, "-preset", "medium", flag, params, *rate, *common]
    return ["-c:v", encoder.name, *rate, *common]


def run_ffmpeg(command: list[str], timeout: float) -> None:
    started = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"ffmpeg timed out after {timeout:.0f} s") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed ({result.returncode}):\n{tail}")
    log.debug("ffmpeg finished in %.1f s", time.perf_counter() - started)


def write_vignette(path: Path, width: int, height: int, strength: float = 0.55) -> Path:
    import struct
    import zlib

    if path.exists():
        return path
    y, x = np.mgrid[0:height, 0:width]
    radius = np.sqrt(((x - width / 2) / (width / 2)) ** 2 + ((y - height / 2) / (height / 2)) ** 2) / np.sqrt(2)
    alpha = (np.clip((radius - 0.45) / 0.55, 0, 1) ** 2 * strength * 255).astype(np.uint8)
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., 3] = alpha
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def segment_command(ffmpeg: Path, variant: SegmentVariant, edge: float, fmt: VideoFormat, encoder: EncoderChoice, vignette: Path, out: Path) -> list[str]:
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    for piece in variant.pieces:
        if piece.is_image:
            cmd += ["-loop", "1", "-framerate", str(fmt.fps), "-t", f"{piece.duration:.3f}", "-i", piece.path]
        else:
            cmd += ["-ss", f"{piece.start:.3f}", "-t", f"{piece.duration:.3f}", "-i", piece.path]
    cmd += ["-loop", "1", "-framerate", str(fmt.fps), "-i", str(vignette)]
    vignette_index = len(variant.pieces)

    w, h, fps = fmt.width, fmt.height, fmt.fps
    parts = []
    for i, piece in enumerate(variant.pieces):
        chain = f"[{i}:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h},setsar=1"
        if piece.mirror:
            chain += ",hflip"
        chain += f",format=yuv420p,setpts=PTS-STARTPTS[p{i}]"
        parts.append(chain)

    current, offset = "p0", variant.pieces[0].duration
    for i, fade in enumerate(variant.inner_crossfades, start=1):
        offset -= fade
        parts.append(f"[{current}][p{i}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[x{i}]")
        current = f"x{i}"
        offset += variant.pieces[i].duration

    m, g = variant.motion, variant.grade
    zoom = f"{m.zoom_base:.4f}+{m.zoom_amplitude:.4f}*sin(2*PI*it/{m.zoom_period:.2f}+{m.zoom_phase:.3f})"
    pan_x = f"iw/2-(iw/zoom/2)+{m.pan_x:.2f}*sin(2*PI*it/{m.pan_period:.2f}+{m.pan_phase:.3f})"
    pan_y = f"ih/2-(ih/zoom/2)+{m.pan_y:.2f}*cos(2*PI*it/{m.pan_period:.2f}+{m.pan_phase:.3f})"
    parts.append(
        f"[{current}]trim=duration={variant.length:.3f},"
        f"zoompan=z='{zoom}':x='{pan_x}':y='{pan_y}':d=1:s={w}x{h}:fps={fps},"
        f"eq=brightness={g.brightness:.4f}:contrast={g.contrast:.4f}:saturation={g.saturation:.4f}:gamma_r={g.gamma_r:.4f}:gamma_b={g.gamma_b:.4f}[graded]"
    )
    parts.append(f"[graded][{vignette_index}:v]overlay=format=auto:shortest=1,format=yuv420p[v]")

    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-t", f"{variant.length:.3f}", "-an"]
    cmd += encode_args(encoder, fmt, [edge, variant.length - edge])
    cmd += ["-f", "mpegts", str(out)]
    return cmd


def split_segment(ffmpeg: Path, source: Path, edge: float, body: float, out_dir: Path, name: str) -> tuple[Path, Path, Path]:
    pattern = out_dir / f"{name}_%d.ts"
    run_ffmpeg(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v", "-c", "copy",
         "-f", "segment", "-segment_times", f"{edge:.3f},{edge + body:.3f}", "-reset_timestamps", "1", str(pattern)],
        timeout=600,
    )
    files = tuple(out_dir / f"{name}_{i}.ts" for i in range(3))
    if not all(f.exists() for f in files):
        raise RenderError(f"splitting {source.name} did not produce head/body/tail")
    return files  # type: ignore[return-value]


def transition_command(ffmpeg: Path, tail: Path, head: Path, edge: float, fmt: VideoFormat, encoder: EncoderChoice, out: Path) -> list[str]:
    graph = f"[0:v][1:v]xfade=transition=fade:duration={edge:.3f}:offset=0,format=yuv420p[v]"
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(tail), "-i", str(head),
            "-filter_complex", graph, "-map", "[v]", "-t", f"{edge:.3f}", *encode_args(encoder, fmt, [0.0]), "-f", "mpegts", str(out)]


def fade_in_command(ffmpeg: Path, head: Path, edge: float, fmt: VideoFormat, encoder: EncoderChoice, out: Path) -> list[str]:
    fade = min(FADE_IN_S, edge)
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(head), "-vf", f"fade=t=in:d={fade:.3f},format=yuv420p",
            "-t", f"{edge:.3f}", *encode_args(encoder, fmt, [0.0]), "-f", "mpegts", str(out)]


def render_video(
    ffmpeg: Path,
    ffprobe: Path,
    variants: list[SegmentVariant],
    timeline: Timeline,
    audio: Path,
    duration: float,
    fmt: VideoFormat,
    encoder: EncoderChoice,
    work_dir: Path,
    out_path: Path,
    cache_dir: Path,
) -> VideoRenderResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    edge, body = timeline.edge, timeline.body
    vignette = write_vignette(cache_dir / f"vignette_{fmt.width}x{fmt.height}.png", fmt.width, fmt.height)
    tmp_out = out_path.with_suffix(".tmp.mp4")
    try:
        started = time.perf_counter()
        parts: dict[int, tuple[Path, Path, Path]] = {}
        for index in sorted(set(timeline.order)):
            variant = variants[index]
            full = work_dir / f"variant_{index}.ts"
            log.info("rendering visual variant %d (%.0f s, %d pieces)", index, variant.length, len(variant.pieces))
            run_ffmpeg(segment_command(ffmpeg, variant, edge, fmt, encoder, vignette, full), timeout=max(600, variant.length * 4))
            parts[index] = split_segment(ffmpeg, full, edge, body, work_dir, f"v{index}")
            full.unlink(missing_ok=True)
        timings["segments_s"] = time.perf_counter() - started

        started = time.perf_counter()
        sequence: list[Path] = []
        first = timeline.order[0]
        intro = work_dir / "intro.ts"
        run_ffmpeg(fade_in_command(ffmpeg, parts[first][0], edge, fmt, encoder, intro), timeout=300)
        sequence += [intro, parts[first][1]]
        transitions: dict[tuple[int, int], Path] = {}
        for previous, current in zip(timeline.order, timeline.order[1:], strict=False):
            key = (previous, current)
            if key not in transitions:
                path = work_dir / f"t_{previous}_{current}.ts"
                run_ffmpeg(transition_command(ffmpeg, parts[previous][2], parts[current][0], edge, fmt, encoder, path), timeout=300)
                transitions[key] = path
            sequence += [transitions[key], parts[current][1]]
        sequence.append(parts[timeline.order[-1]][2])
        timings["transitions_s"] = time.perf_counter() - started

        lengths = [probe(ffprobe, p).duration or 0.0 for p in dict.fromkeys(sequence)]
        length_of = dict(zip(dict.fromkeys(sequence), lengths, strict=True))
        junctions, t = [], 0.0
        for item in sequence[:-1]:
            t += length_of[item]
            if t < duration - 1:
                junctions.append(round(t, 3))

        concat_list = work_dir / "concat.txt"
        concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in sequence), encoding="utf-8")
        started = time.perf_counter()
        audio_args = ["-c:a", "aac", "-b:a", fmt.audio_bitrate] if fmt.audio_codec == "aac" else ["-c:a", "libopus", "-b:a", fmt.audio_bitrate]
        tag = ["-tag:v", "hvc1"] if "hevc" in encoder.name or "265" in encoder.name else []
        run_ffmpeg(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *tag, *audio_args,
             "-t", f"{duration:.3f}", "-movflags", "+faststart", str(tmp_out)],
            timeout=max(900, duration),
        )
        tmp_out.replace(out_path)
        timings["assemble_s"] = time.perf_counter() - started
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        tmp_out.unlink(missing_ok=True)

    final = probe(ffprobe, out_path)
    return VideoRenderResult(out_path, final.duration or 0.0, junctions, timings)
