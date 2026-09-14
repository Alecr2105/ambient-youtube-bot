from __future__ import annotations

import math

import numpy as np
from scipy import signal

from app.audio.processor.filters import SosFilter, biquad_high_pass, biquad_high_shelf

ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
TRUE_PEAK_OVERSAMPLE = 4


def k_weighting_sos(sample_rate: int) -> np.ndarray:
    # ITU-R BS.1770-4 pre-filter and RLB filter, derived for any sample rate.
    shelf = biquad_high_shelf(sample_rate, 1681.9744509555319, 3.99984385397, 0.7071752369554193)
    highpass = biquad_high_pass(sample_rate, 38.13547087613982, 0.5003270373253953)
    return np.vstack([shelf, highpass])


class LoudnessMeter:
    """Streaming BS.1770-4 integrated loudness (400 ms blocks, 75 % overlap, gated)."""

    def __init__(self, sample_rate: int, channels: int = 2):
        self.sample_rate = sample_rate
        self.segment = int(round(sample_rate * 0.1))
        self.filter = SosFilter(k_weighting_sos(sample_rate), channels)
        self.pending = np.zeros((0, channels))
        self.segment_energy: list[float] = []

    def add(self, block: np.ndarray) -> None:
        weighted = np.concatenate([self.pending, self.filter(block)])
        full = len(weighted) // self.segment
        if full:
            usable = weighted[: full * self.segment]
            energy = np.square(usable).reshape(full, self.segment, -1).sum(axis=(1, 2))
            self.segment_energy.extend(energy.tolist())
        self.pending = weighted[full * self.segment :]

    def block_loudness(self) -> np.ndarray:
        energy = np.asarray(self.segment_energy)
        if len(energy) < 4:
            return np.array([])
        z = np.convolve(energy, np.ones(4), mode="valid") / (4 * self.segment)
        with np.errstate(divide="ignore"):
            return -0.691 + 10 * np.log10(z)

    def integrated(self) -> float:
        blocks = self.block_loudness()
        if not len(blocks):
            return -math.inf
        z = 10 ** ((blocks + 0.691) / 10)
        gated = blocks > ABSOLUTE_GATE_LUFS
        if not gated.any():
            return -math.inf
        relative = -0.691 + 10 * math.log10(z[gated].mean()) + RELATIVE_GATE_LU
        gated &= blocks > relative
        return -0.691 + 10 * math.log10(z[gated].mean())


def true_peak_per_sample(block: np.ndarray) -> np.ndarray:
    """Max absolute value of the 4x oversampled signal, folded back to one value per input sample."""
    upsampled = signal.resample_poly(block, TRUE_PEAK_OVERSAMPLE, 1, axis=0)
    folded = np.abs(upsampled[: len(block) * TRUE_PEAK_OVERSAMPLE])
    return folded.reshape(len(block), TRUE_PEAK_OVERSAMPLE, -1).max(axis=(1, 2))


class TruePeakMeter:
    """Streaming true peak. Samples near a block edge lack resampling context, so the last
    CONTEXT samples are measured on the next call, with the preceding CONTEXT as history."""

    CONTEXT = 64

    def __init__(self):
        self.history = np.zeros((0, 2))
        self.peak = 0.0

    def add(self, block: np.ndarray) -> None:
        data = np.concatenate([self.history, block])
        start = max(len(self.history) - self.CONTEXT, 0)
        end = len(data) - self.CONTEXT
        if end > start:
            self.peak = max(self.peak, float(true_peak_per_sample(data)[start:end].max()))
            self.history = data[end - self.CONTEXT :] if end >= self.CONTEXT else data
        else:
            self.history = data

    def finish(self) -> float:
        if len(self.history):
            start = max(len(self.history) - self.CONTEXT, 0)
            self.peak = max(self.peak, float(true_peak_per_sample(self.history)[start:].max()))
        return self.peak

    @staticmethod
    def to_db(value: float) -> float:
        return 20 * math.log10(value) if value > 0 else -math.inf
