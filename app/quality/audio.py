from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio.processor.loudness import LoudnessMeter, TruePeakMeter
from app.quality.report import QualityReport

FEATURE_HOP_S = 0.1
SILENCE_FRAME_S = 0.05
LOOP_BANDS_HZ = (100, 300, 800, 2000, 4500, 9000, 16000)


@dataclass(frozen=True)
class AudioThresholds:
    expected_duration_s: float | None = None
    duration_tolerance_s: float = 1.0
    target_lufs: float = -18.0
    loudness_tolerance_lu: float = 1.0
    max_true_peak_dbtp: float = -1.0
    true_peak_tolerance_db: float = 0.1
    silence_dbfs: float = -60.0
    max_silence_s: float = 2.0
    ignore_edges_s: float = 25.0
    loop_min_lag_s: float = 20.0
    loop_max_correlation: float = 0.35
    click_ratio: float = 2.5
    #: Only for ambients meant for studying: how far the loudest 3 s may sit above the usual
    #: level. A thunder clap or a breaking wave pulls attention away from the page.
    max_short_term_jump_lu: float | None = None


SHORT_TERM_WINDOW_S = 3.0


def short_term_loudness(meter: LoudnessMeter, window_s: float = SHORT_TERM_WINDOW_S) -> np.ndarray:
    """BS.1770-4 short-term loudness (sliding `window_s`, 100 ms hop) from a fed meter."""
    energy = np.asarray(meter.segment_energy)
    window = int(round(window_s / 0.1))
    if len(energy) < window:
        return np.array([])
    mean = np.convolve(energy, np.ones(window), mode="valid") / (window * meter.segment)
    with np.errstate(divide="ignore"):
        values = -0.691 + 10 * np.log10(mean)
    return values[np.isfinite(values)]


class _FeatureExtractor:
    """Per-100 ms log band energies (for loop detection) and per-50 ms RMS (for silence)."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.hop = int(round(sample_rate * FEATURE_HOP_S))
        self.silence_frame = int(round(sample_rate * SILENCE_FRAME_S))
        freqs = np.fft.rfftfreq(self.hop, 1 / sample_rate)
        edges = [e for e in LOOP_BANDS_HZ if e < sample_rate / 2]
        self.band_masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:], strict=False)]
        self.window = np.hanning(self.hop)
        self.pending = np.zeros(0)
        self.bands: list[np.ndarray] = []
        self.rms: list[np.ndarray] = []

    def add(self, mono: np.ndarray) -> None:
        data = np.concatenate([self.pending, mono])
        full = len(data) // self.hop
        frames = data[: full * self.hop].reshape(full, self.hop)
        if full:
            power = np.abs(np.fft.rfft(frames * self.window, axis=1)) ** 2
            energies = np.column_stack([power[:, mask].sum(axis=1) for mask in self.band_masks])
            self.bands.append(np.log10(energies + 1e-12))
            per_hop = self.hop // self.silence_frame
            sub = frames[:, : per_hop * self.silence_frame].reshape(full * per_hop, self.silence_frame)
            self.rms.append(np.sqrt(np.mean(np.square(sub), axis=1)))
        self.pending = data[full * self.hop :]

    def band_matrix(self) -> np.ndarray:
        return np.vstack(self.bands) if self.bands else np.zeros((0, len(self.band_masks)))

    def rms_db(self) -> np.ndarray:
        rms = np.concatenate(self.rms) if self.rms else np.zeros(0)
        return 20 * np.log10(np.maximum(rms, 1e-10))


def loop_correlation(bands: np.ndarray, min_lag_frames: int) -> tuple[float, int]:
    """Highest normalized autocorrelation of band-energy changes beyond `min_lag_frames`.

    Differencing removes slow level trends, so this responds to repeated *fine structure*
    (a copy of the same material), not to a sound that merely stays similar.
    """
    if len(bands) < 2 * min_lag_frames + 2:
        return 0.0, 0
    changes = np.diff(bands, axis=0)
    changes = (changes - changes.mean(axis=0)) / (changes.std(axis=0) + 1e-12)
    n = len(changes)
    size = 1 << (2 * n - 1).bit_length()
    spectrum = np.fft.rfft(changes, n=size, axis=0)
    acf = np.fft.irfft(np.abs(spectrum) ** 2, n=size, axis=0)[:n].mean(axis=1)
    lags = np.arange(n)
    normalized = acf / (n - lags) / acf[0] * n
    search = normalized[min_lag_frames : n // 2]
    if not len(search):
        return 0.0, 0
    best = int(np.argmax(search))
    return float(search[best]), best + min_lag_frames


def longest_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    padded = np.concatenate([[0], mask.astype(np.int8), [0]])
    changes = np.flatnonzero(np.diff(padded))
    return int((changes[1::2] - changes[::2]).max())


def junction_click_ratios(path: Path, junctions_s: list[float]) -> list[float]:
    """Largest sample step at each junction relative to the signal's own steps in the
    surrounding second. A splice inside broadband noise stays near 1 (inaudible); a jump
    that stands out from the material scores well above it."""
    ratios = []
    with sf.SoundFile(path) as f:
        sr = f.samplerate
        window = int(0.001 * sr)
        guard = int(0.01 * sr)
        for junction in junctions_s:
            center = int(junction * sr)
            start = max(center - sr, 0)
            f.seek(start)
            steps = np.abs(np.diff(f.read(2 * sr, dtype="float64", always_2d=True), axis=0)).max(axis=1)
            local = center - start
            near = steps[max(local - window, 0) : local + window]
            surroundings = np.concatenate([steps[: max(local - guard, 0)], steps[local + guard :]])
            if not len(near) or not len(surroundings):
                ratios.append(0.0)
                continue
            ratios.append(float(near.max() / (np.percentile(surroundings, 99.9) + 1e-9)))
    return ratios


def check_audio(path: Path, thresholds: AudioThresholds, junctions_s: list[float] | None = None) -> QualityReport:
    report = QualityReport("audio")
    info = sf.info(str(path))
    sr = info.samplerate
    loudness, peak = LoudnessMeter(sr, info.channels), TruePeakMeter()
    features = _FeatureExtractor(sr)
    clipped = 0
    with sf.SoundFile(path) as f:
        for block in f.blocks(blocksize=sr * 10, dtype="float64", always_2d=True):
            if block.shape[1] == 1:
                block = np.repeat(block, 2, axis=1)
            loudness.add(block)
            peak.add(block)
            clipped += int(np.count_nonzero(np.abs(block) >= 0.9999))
            features.add(block.mean(axis=1))

    duration = info.frames / sr
    if thresholds.expected_duration_s is not None:
        diff = abs(duration - thresholds.expected_duration_s)
        report.add("duration", diff <= thresholds.duration_tolerance_s, duration, thresholds.expected_duration_s)
    else:
        report.add("duration", duration > 0, duration)

    lufs = loudness.integrated()
    report.add(
        "integrated_loudness",
        math.isfinite(lufs) and abs(lufs - thresholds.target_lufs) <= thresholds.loudness_tolerance_lu,
        lufs, f"{thresholds.target_lufs} ± {thresholds.loudness_tolerance_lu} LU",
    )
    true_peak = TruePeakMeter.to_db(peak.finish())
    tp_limit = thresholds.max_true_peak_dbtp + thresholds.true_peak_tolerance_db
    report.add("true_peak", true_peak <= tp_limit, true_peak, tp_limit)
    report.add("clipping", clipped == 0, clipped, 0, "samples at full scale")

    if thresholds.max_short_term_jump_lu is not None:
        values = short_term_loudness(loudness)
        # Drop the fade in and out: they are quiet by design and would dominate the spread.
        skip = int(thresholds.ignore_edges_s / 0.1)
        inner_st = values[skip : len(values) - skip] if len(values) > 2 * skip + 10 else values
        jump = float(inner_st.max() - np.median(inner_st)) if inner_st.size else 0.0
        report.add(
            "calm_for_study", jump <= thresholds.max_short_term_jump_lu, round(jump, 2),
            thresholds.max_short_term_jump_lu, "loudest 3 s above the usual level (LU)",
        )

    rms_db = features.rms_db()
    edge = int(thresholds.ignore_edges_s / SILENCE_FRAME_S)
    inner = rms_db[edge : len(rms_db) - edge] if len(rms_db) > 2 * edge else rms_db
    silence_s = longest_run(inner < thresholds.silence_dbfs) * SILENCE_FRAME_S
    report.add("silence", silence_s <= thresholds.max_silence_s, silence_s, thresholds.max_silence_s, "longest run below threshold (s)")

    correlation, lag = loop_correlation(features.band_matrix(), int(thresholds.loop_min_lag_s / FEATURE_HOP_S))
    report.add(
        "loop_detection",
        correlation <= thresholds.loop_max_correlation,
        correlation, thresholds.loop_max_correlation,
        f"strongest repetition at lag {lag * FEATURE_HOP_S:.1f} s",
    )

    if junctions_s:
        ratios = junction_click_ratios(path, junctions_s)
        worst = max(ratios)
        report.add("junction_clicks", worst <= thresholds.click_ratio, worst, thresholds.click_ratio, f"{len(ratios)} junctions")

    report.metrics.update({"sample_rate": sr, "channels": info.channels, "frames": info.frames, "loop_lag_s": lag * FEATURE_HOP_S})
    return report
