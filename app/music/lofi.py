"""Lofi music for a video: a playlist of new tracks generated with ACE-Step, mixed over the ambience.

Every video gets its own tracks (never reused), generated locally by ACE-Step v1 3.5B
(Apache-2.0, https://github.com/ace-step/ACE-Step) in ComfyUI: ~90 s of GPU per 3-minute track
on the RTX 4060 laptop. Tracks are levelled to the same loudness, joined with equal-power
crossfades, and the recipe's rendered ambience (rain) plays underneath at `bed_below_lu`.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio.processor.loudness import LoudnessMeter
from app.music.comfy import ComfyServer

log = logging.getLogger(__name__)

MODEL = "ace_step_v1_3.5b.safetensors"
MODEL_NAME = "ACE-Step v1 3.5B"
MODEL_LICENSE = "Apache-2.0"
MODEL_URL = "https://github.com/ace-step/ACE-Step"

COMMON_TAGS = ("lofi hip hop, chill, instrumental, no vocals, relaxing study music, warm vinyl crackle, "
               "soft tape saturation, laid-back mellow drums")
#: The six styles the owner approved on 2026-10-05 (samples in output/previews_ai/).
STYLES = {
    "acoustic_guitar": "mellow acoustic guitar, fingerpicking, soft bass, 72 bpm",
    "rhodes_piano": "soft rhodes electric piano, jazzy chords, upright bass, 75 bpm",
    "rainy_nylon_guitar": "nylon guitar, gentle melody, soft pads, rainy mood, 70 bpm",
    "soft_jazz": "jazzy guitar, muted trumpet far away, brushed drums, 80 bpm",
    "bossa_lofi": "bossa nova guitar, lofi, soft shaker, warm bass, 78 bpm",
    "night_piano": "slow felt piano, ambient pads, night mood, 68 bpm",
}
FADE_IN_S = 3.0
FADE_OUT_S = 8.0
#: Sample peak ceiling of the final mix; the gate checks true peak at -1 dBTP.
LIMIT_DB = -2.0


class MusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackPlan:
    index: int
    style: str
    seed: int

    @property
    def tags(self) -> str:
        return f"{COMMON_TAGS}, {STYLES[self.style]}"


@dataclass
class Track:
    plan: TrackPlan
    path: Path
    duration: float
    gain_db: float


def tracks_needed(duration: float, track_s: float, crossfade_s: float) -> int:
    """n tracks overlapping by `crossfade_s` cover n*T - (n-1)*X seconds."""
    return max(1, math.ceil((duration - crossfade_s) / (track_s - crossfade_s)))


def plan_tracks(duration: float, track_s: float, crossfade_s: float, styles: list[str], seed: int) -> list[TrackPlan]:
    """Styles in shuffled rounds, never the same style twice in a row; a new seed for every track."""
    styles = styles or list(STYLES)
    unknown = set(styles) - set(STYLES)
    if unknown:
        raise MusicError(f"unknown music styles {sorted(unknown)}; use {sorted(STYLES)}")
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x10F1]))
    order: list[str] = []
    n = tracks_needed(duration, track_s, crossfade_s)
    while len(order) < n:
        round_ = [styles[i] for i in rng.permutation(len(styles))]
        if order and len(round_) > 1 and round_[0] == order[-1]:
            round_[0], round_[-1] = round_[-1], round_[0]
        order += round_
    return [TrackPlan(i, style, int(rng.integers(2**31))) for i, style in enumerate(order[:n])]


def ace_step_graph(tags: str, seconds: float, seed: int, prefix: str) -> dict:
    """ComfyUI API graph of the official ACE-Step text-to-music workflow, instrumental."""
    return {
        "40": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": MODEL}},
        "14": {"class_type": "TextEncodeAceStepAudio", "inputs": {"clip": ["40", 1], "tags": tags, "lyrics": "[inst]", "lyrics_strength": 0.99}},
        "44": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["14", 0]}},
        "17": {"class_type": "EmptyAceStepLatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "51": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["40", 0], "shift": 5.0}},
        "50": {"class_type": "LatentOperationTonemapReinhard", "inputs": {"multiplier": 1.0}},
        "49": {"class_type": "LatentApplyOperationCFG", "inputs": {"model": ["51", 0], "operation": ["50", 0]}},
        "52": {"class_type": "KSampler", "inputs": {"model": ["49", 0], "positive": ["14", 0], "negative": ["44", 0],
                                                   "latent_image": ["17", 0], "seed": seed, "steps": 50, "cfg": 5.0,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "18": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["52", 0], "vae": ["40", 2]}},
        "59": {"class_type": "SaveAudio", "inputs": {"audio": ["18", 0], "filename_prefix": prefix}},
    }


def level_gain_db(path: Path, target_lufs: float) -> float:
    data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    meter = LoudnessMeter(sr, 2)
    meter.add(data)
    loudness = meter.integrated()
    if not math.isfinite(loudness) or loudness < -50:
        raise MusicError(f"{path.name} is silent or nearly so ({loudness:.1f} LUFS)")
    return target_lufs - loudness


def generate_tracks(server: ComfyServer, plans: list[TrackPlan], track_s: float, target_lufs: float, work: Path) -> list[Track]:
    """Generates the missing tracks into `work` (kept there, so a resumed video does not redo them)."""
    work.mkdir(parents=True, exist_ok=True)
    tracks = []
    for plan in plans:
        path = work / f"track_{plan.index:03d}_{plan.style}.flac"
        if not path.exists():
            files = server.run(ace_step_graph(plan.tags, track_s, plan.seed, f"ambient_bot/{work.parent.name}_{plan.index:03d}"))
            if not files:
                raise MusicError(f"ACE-Step returned no audio for track {plan.index}")
            shutil.move(str(files[0]), path)
            log.info("music track %d/%d (%s) generated", plan.index + 1, len(plans), plan.style)
        tracks.append(Track(plan, path, sf.info(str(path)).duration, level_gain_db(path, target_lufs)))
    return tracks


def junctions(tracks: list[Track], crossfade_s: float, duration: float) -> list[float]:
    """Centre of each crossfade on the programme timeline."""
    points, start = [], 0.0
    for track in tracks[:-1]:
        start += track.duration - crossfade_s
        if start + crossfade_s / 2 < duration - 1:
            points.append(round(start + crossfade_s / 2, 3))
    return points


def mix_command(ffmpeg: Path, tracks: list[Track], bed: Path, bed_gain_db: float, duration: float, crossfade_s: float,
                sample_rate: int, out: Path) -> list[str]:
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    for track in tracks:
        cmd += ["-i", str(track.path)]
    cmd += ["-i", str(bed)]
    parts = [f"[{i}:a]aresample={sample_rate},aformat=channel_layouts=stereo,volume={t.gain_db:.3f}dB[t{i}]" for i, t in enumerate(tracks)]
    current = "t0"
    for i in range(1, len(tracks)):
        parts.append(f"[{current}][t{i}]acrossfade=d={crossfade_s:.3f}:c1=qsin:c2=qsin[x{i}]")
        current = f"x{i}"
    fade_out_at = max(duration - FADE_OUT_S, 0.0)
    parts.append(f"[{current}]atrim=0:{duration:.3f},afade=t=in:d={FADE_IN_S},afade=t=out:st={fade_out_at:.3f}:d={FADE_OUT_S}[music]")
    parts.append(f"[{len(tracks)}:a]aresample={sample_rate},volume={bed_gain_db:.3f}dB[bed]")
    # level=disabled: the limiter only catches peaks, it must not raise the whole mix to the ceiling.
    parts.append(f"[music][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit={10 ** (LIMIT_DB / 20):.4f}:level=disabled[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-t", f"{duration:.3f}",
            "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", str(out)]
    return cmd


def produce_music(ffmpeg: Path, server_root: Path, server_url: str, plans: list[TrackPlan], bed: Path, bed_below_lu: float,
                  duration: float, track_s: float, crossfade_s: float, target_lufs: float, sample_rate: int,
                  work: Path, out: Path) -> tuple[list[Track], list[float]]:
    """Generates the tracks and writes the final mix (music at `target_lufs`, ambience below) to `out`."""
    with ComfyServer(server_root, server_url) as server:
        tracks = generate_tracks(server, plans, track_s, target_lufs, work)
    covered = sum(t.duration for t in tracks) - crossfade_s * (len(tracks) - 1)
    if covered < duration:
        raise MusicError(f"tracks cover {covered:.0f} s of {duration:.0f} s")
    tmp = out.with_suffix(".tmp.flac")
    result = subprocess.run(mix_command(ffmpeg, tracks, bed, -bed_below_lu, duration, crossfade_s, sample_rate, tmp),
                            capture_output=True, text=True, timeout=max(1800, duration))
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise MusicError(f"music mix failed: {result.stderr.strip()[-1500:]}")
    tmp.replace(out)
    return tracks, junctions(tracks, crossfade_s, duration)


def describe(tracks: list[Track]) -> list[dict]:
    return [{**asdict(t.plan), "tags": t.plan.tags, "file": t.path.name, "duration_s": round(t.duration, 2),
             "gain_db": round(t.gain_db, 2)} for t in tracks]
