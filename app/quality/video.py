from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.quality.report import QualityReport
from app.video.probe import probe

PROBE_W, PROBE_H = 160, 90
MAX_JUNCTIONS = 300


@dataclass(frozen=True)
class VideoThresholds:
    expected_duration_s: float
    width: int
    height: int
    fps: int
    duration_tolerance_s: float = 1.0
    ignore_edges_s: float = 5.0
    black_min_duration_s: float = 0.5
    junction_ratio: float = 3.0
    junction_min_diff: float = 6.0


def _frames(ffmpeg: Path, path: Path, at: float, count: int) -> np.ndarray:
    result = subprocess.run(
        [str(ffmpeg), "-v", "error", "-ss", f"{max(at, 0):.3f}", "-i", str(path), "-frames:v", str(count),
         "-vf", f"scale={PROBE_W}:{PROBE_H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60, check=True,
    )
    frames = np.frombuffer(result.stdout, np.uint8)
    return frames[: count * PROBE_W * PROBE_H].reshape(-1, PROBE_H, PROBE_W).astype(np.float32)


WINDOW_FRAMES = 4


def _max_step(frames: np.ndarray) -> float:
    if len(frames) < 2:
        return 0.0
    return float(np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)).max())


def junction_scores(ffmpeg: Path, path: Path, junctions: list[float], fps: int) -> list[tuple[float, float, float]]:
    """(time, largest frame step in a small window around the junction, largest step nearby).

    A window rather than one exact frame pair: seeking lands within a frame or two of the
    requested time, so a single pair can miss the cut entirely.
    """
    scores = []
    lead = (WINDOW_FRAMES / 2) / fps
    for t in junctions:
        across = _max_step(_frames(ffmpeg, path, t - lead, WINDOW_FRAMES))
        nearby = max(_max_step(_frames(ffmpeg, path, t - 1.5, WINDOW_FRAMES)), _max_step(_frames(ffmpeg, path, t + 1.5, WINDOW_FRAMES)))
        scores.append((t, across, nearby))
    return scores


def black_intervals(ffmpeg: Path, path: Path, min_duration: float) -> list[tuple[float, float]]:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-nostats", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,blackdetect=d={min_duration}:pix_th=0.08", "-f", "null", "-"],
        capture_output=True, text=True, timeout=7200,
    )
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", result.stderr)]


def check_video(ffmpeg: Path, ffprobe: Path, path: Path, thresholds: VideoThresholds, junctions: list[float]) -> QualityReport:
    report = QualityReport("video")
    info = probe(ffprobe, path)
    duration = info.duration or 0.0
    report.add("duration", abs(duration - thresholds.expected_duration_s) <= thresholds.duration_tolerance_s, duration, thresholds.expected_duration_s)
    report.add("resolution", (info.width, info.height) == (thresholds.width, thresholds.height), f"{info.width}x{info.height}", f"{thresholds.width}x{thresholds.height}")
    report.add("fps", info.fps is not None and abs(info.fps - thresholds.fps) < 0.01, info.fps, thresholds.fps)
    report.add("audio_stream", info.has_audio, info.has_audio, True)

    edge = thresholds.ignore_edges_s
    blacks = [(a, b) for a, b in black_intervals(ffmpeg, path, thresholds.black_min_duration_s) if b > edge and a < duration - edge]
    report.add("black_frames", not blacks, len(blacks), 0, f"intervals: {blacks[:5]}")

    if junctions:
        sample = junctions if len(junctions) <= MAX_JUNCTIONS else [junctions[i] for i in np.linspace(0, len(junctions) - 1, MAX_JUNCTIONS).astype(int)]
        scores = junction_scores(ffmpeg, path, sample, thresholds.fps)
        bad = [(t, a, n) for t, a, n in scores if a > max(thresholds.junction_ratio * n, thresholds.junction_min_diff)]
        worst = max(scores, key=lambda s: s[1] - s[2])
        report.add("junction_jumps", not bad, len(bad), 0, f"{len(scores)} junctions checked; worst at {worst[0]:.1f}s step={worst[1]:.2f} nearby={worst[2]:.2f}")
    report.metrics.update({"codec": info.codec, "junction_count": len(junctions)})
    return report
