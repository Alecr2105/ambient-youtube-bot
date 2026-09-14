from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.audio.generators.base import Generator, SmoothRandom
from app.audio.generators.registry import ALL_LAYER_GENERATORS, EVENT_GENERATORS
from app.audio.mixer.events import EventOccurrence, schedule_events
from app.audio.processor.dynamics import Compressor, TruePeakLimiter, db_to_gain, fade_gains
from app.audio.processor.filters import FilterChain, SosFilter, biquad_high_shelf, biquad_low_shelf, butter_sos
from app.audio.processor.loudness import LoudnessMeter, TruePeakMeter
from app.audio.recipe import Layer, Recipe

log = logging.getLogger(__name__)

BLOCK_SECONDS = 10.0
LIMITER_MARGIN_DB = 0.3
LOUDNESS_TOLERANCE_LU = 0.5
MAX_MASTER_ATTEMPTS = 3


@dataclass
class RenderResult:
    path: Path
    duration_s: float
    sample_rate: int
    frames: int
    premaster_lufs: float
    integrated_lufs: float
    true_peak_dbtp: float
    master_gain_db: float
    events: list[dict[str, Any]]
    junctions_s: list[float] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


def apply_width(block: np.ndarray, width: float) -> np.ndarray:
    if width == 1.0:
        return block
    mid = (block[:, 0] + block[:, 1]) / 2
    side = (block[:, 0] - block[:, 1]) / 2 * width
    return np.column_stack([mid + side, mid - side])


def build_eq(layer: Layer, sample_rate: int) -> FilterChain:
    eq = layer.eq
    filters = []
    if eq.highpass_hz:
        filters.append(SosFilter(butter_sos(sample_rate, "highpass", eq.highpass_hz, 2)))
    if eq.lowpass_hz:
        filters.append(SosFilter(butter_sos(sample_rate, "lowpass", eq.lowpass_hz, 2)))
    if eq.low_shelf:
        filters.append(SosFilter(biquad_low_shelf(sample_rate, *eq.low_shelf)))
    if eq.high_shelf:
        filters.append(SosFilter(biquad_high_shelf(sample_rate, *eq.high_shelf)))
    return FilterChain(filters)


@dataclass
class LayerRuntime:
    spec: Layer
    generator: Generator
    eq: FilterChain
    automation: SmoothRandom
    gain_db: float

    def render(self, start: int, frames: int, intensity: np.ndarray) -> np.ndarray:
        block = self.eq(self.generator.render(frames))
        gain_db = self.gain_db + self.spec.automation.depth_db * self.automation.at(start, frames)
        gain_db = gain_db + self.spec.intensity_depth_db * intensity
        return apply_width(block, self.spec.width) * db_to_gain(gain_db)[:, None]


class Mixer:
    def __init__(self, recipe: Recipe, instance: dict[str, Any], duration_s: float, sample_rate: int):
        self.recipe = recipe
        self.sample_rate = sample_rate
        self.frames = int(round(duration_s * sample_rate))
        root = np.random.SeedSequence([instance["seed"], 0xB10C])
        layer_seeds = root.spawn(len(recipe.layers))
        intensity_seed, events_seed = root.spawn(2)

        self.intensity = SmoothRandom(
            np.random.default_rng(intensity_seed), sample_rate, duration_s,
            recipe.intensity.min_period_s, recipe.intensity.max_period_s,
        )
        self.layers = []
        for spec, resolved, seed in zip(recipe.layers, instance["layers"], layer_seeds, strict=True):
            if resolved.get("skipped"):
                continue
            generator_seed, automation_seed = seed.spawn(2)
            generator = ALL_LAYER_GENERATORS[resolved["generator"]].create(sample_rate, generator_seed, duration_s, **resolved["params"])
            automation = SmoothRandom(
                np.random.default_rng(automation_seed), sample_rate, duration_s,
                spec.automation.min_period_s, spec.automation.max_period_s,
            )
            self.layers.append(LayerRuntime(spec, generator, build_eq(spec, sample_rate), automation, resolved["gain_db"]))

        self.occurrences = schedule_events(recipe, instance, duration_s, sample_rate, self.intensity, events_seed)
        self.events = {spec.name: (spec, resolved) for spec, resolved in zip(recipe.events, instance["events"], strict=True)}
        self.active: list[tuple[int, np.ndarray]] = []
        self.next_occurrence = 0

    def junctions(self) -> list[int]:
        return sorted(j for layer in self.layers for j in getattr(layer.generator, "junctions", []))

    def _event_audio(self, occurrence: EventOccurrence) -> np.ndarray:
        spec, resolved = self.events[occurrence.event]
        rng = np.random.default_rng(occurrence.seed)
        params = {}
        for key, value in resolved["params"].items():
            is_range = isinstance(value, list | tuple) and len(value) == 2 and all(isinstance(v, int | float) for v in value)
            params[key] = (float(rng.uniform(*value)) if value[1] > value[0] else float(value[0])) if is_range else value
        generator = EVENT_GENERATORS[resolved["generator"]](self.sample_rate, rng, **params)
        return apply_width(generator.render(), spec.width) * db_to_gain(occurrence.gain_db)

    def render_block(self, start: int, frames: int) -> np.ndarray:
        end = start + frames
        intensity = self.intensity.at(start, frames)
        block = np.zeros((frames, 2))
        for layer in self.layers:
            block += layer.render(start, frames, intensity)

        while self.next_occurrence < len(self.occurrences) and self.occurrences[self.next_occurrence].onset < end:
            occurrence = self.occurrences[self.next_occurrence]
            self.active.append((occurrence.onset, self._event_audio(occurrence)))
            self.next_occurrence += 1
        still_active = []
        for onset, audio in self.active:
            lo, hi = max(onset, start), min(onset + len(audio), end)
            if lo < hi:
                block[lo - start : hi - start] += audio[lo - onset : hi - onset]
            if onset + len(audio) > end:
                still_active.append((onset, audio))
        self.active = still_active
        return block


def _progress(label: str, done: int, total: int, last: list[int]) -> None:
    pct = int(done * 100 / total)
    if pct >= last[0] + 10:
        last[0] = pct - pct % 10
        log.info("%s %d%%", label, last[0])


def render_premaster(mixer: Mixer, path: Path, block_frames: int) -> float:
    meter = LoudnessMeter(mixer.sample_rate)
    last = [-10]
    with sf.SoundFile(path, "w", mixer.sample_rate, 2, subtype="FLOAT", format="RF64") as out:
        for start in range(0, mixer.frames, block_frames):
            frames = min(block_frames, mixer.frames - start)
            block = mixer.render_block(start, frames)
            meter.add(block)
            out.write(block.astype(np.float32))
            _progress("mix", start + frames, mixer.frames, last)
    return meter.integrated()


def master(
    premaster: Path,
    out_path: Path,
    recipe: Recipe,
    gain_db: float,
    target_lufs: float,
    target_true_peak: float,
    block_frames: int,
) -> tuple[float, float]:
    info = sf.info(str(premaster))
    sr, total = info.samplerate, info.frames
    compressor = Compressor(sr, target_lufs + recipe.master.compressor_threshold_lu, recipe.master.compressor_ratio)
    limiter = TruePeakLimiter(sr, target_true_peak - LIMITER_MARGIN_DB)
    loudness, peak = LoudnessMeter(sr), TruePeakMeter()
    fade_in, fade_out = int(recipe.master.fade_in_s * sr), int(recipe.master.fade_out_s * sr)
    gain = float(db_to_gain(gain_db))
    written = 0
    last = [-10]

    def emit(chunk: np.ndarray, out: sf.SoundFile) -> None:
        nonlocal written
        if not len(chunk):
            return
        chunk = chunk * fade_gains(written, len(chunk), total, fade_in, fade_out)[:, None]
        loudness.add(chunk)
        peak.add(chunk)
        out.write(chunk)
        written += len(chunk)

    with sf.SoundFile(premaster) as source, sf.SoundFile(out_path, "w", sr, 2, subtype="PCM_24", format="FLAC") as out:
        for block in source.blocks(blocksize=block_frames, dtype="float64", always_2d=True):
            emit(limiter(compressor(block * gain)), out)
            _progress("master", written, total, last)
        emit(limiter.flush(), out)
    return loudness.integrated(), TruePeakMeter.to_db(peak.finish())


def render_program(
    recipe: Recipe,
    instance: dict[str, Any],
    duration_s: float,
    sample_rate: int,
    target_lufs: float,
    target_true_peak: float,
    work_dir: Path,
    out_path: Path,
    block_seconds: float = BLOCK_SECONDS,
) -> RenderResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    premaster = work_dir / "premaster.rf64"
    tmp_out = out_path.with_suffix(".tmp.flac")
    block_frames = int(block_seconds * sample_rate)
    timings: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        mixer = Mixer(recipe, instance, duration_s, sample_rate)
        premaster_lufs = render_premaster(mixer, premaster, block_frames)
        timings["mix_s"] = time.perf_counter() - t0
        if not math.isfinite(premaster_lufs):
            raise RuntimeError("premaster is silent")

        t1 = time.perf_counter()
        gain_db = target_lufs - premaster_lufs
        for attempt in range(MAX_MASTER_ATTEMPTS):
            lufs, true_peak = master(premaster, tmp_out, recipe, gain_db, target_lufs, target_true_peak, block_frames)
            log.info("master attempt %d: %.2f LUFS, %.2f dBTP (gain %.2f dB)", attempt + 1, lufs, true_peak, gain_db)
            if abs(lufs - target_lufs) <= LOUDNESS_TOLERANCE_LU:
                break
            gain_db += target_lufs - lufs
        timings["master_s"] = time.perf_counter() - t1
        tmp_out.replace(out_path)
    finally:
        premaster.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)

    return RenderResult(
        path=out_path,
        duration_s=mixer.frames / sample_rate,
        sample_rate=sample_rate,
        frames=mixer.frames,
        premaster_lufs=premaster_lufs,
        integrated_lufs=lufs,
        true_peak_dbtp=true_peak,
        master_gain_db=gain_db,
        events=[o.as_dict(sample_rate) for o in mixer.occurrences],
        junctions_s=[j / sample_rate for j in mixer.junctions()],
        timings=timings,
    )
