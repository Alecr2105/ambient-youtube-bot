from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

from app.audio.processor.loudness import true_peak_per_sample


def db_to_gain(db: float | np.ndarray) -> float | np.ndarray:
    return 10 ** (np.asarray(db) / 20)


class Compressor:
    """Slow feed-forward RMS compressor. Threshold is in dBFS of the signal it receives."""

    def __init__(self, sample_rate: int, threshold_db: float, ratio: float, window_ms: float = 300.0):
        self.threshold_db = threshold_db
        self.slope = 1 - 1 / ratio
        coeff = math.exp(-1 / (sample_rate * window_ms / 1000))
        self.b = [1 - coeff]
        self.a = [1, -coeff]
        self.zi = np.zeros(1)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        power = np.mean(np.square(block), axis=1)
        envelope, self.zi = signal.lfilter(self.b, self.a, power, zi=self.zi)
        level_db = 10 * np.log10(np.maximum(envelope, 1e-12))
        reduction_db = -np.maximum(level_db - self.threshold_db, 0) * self.slope
        return block * db_to_gain(reduction_db)[:, None]


class TruePeakLimiter:
    """Lookahead brickwall limiter on 4x-oversampled peaks.

    gain = moving_average(min_filter(required, 2L+1), L+1): the average of a window
    that lies inside the min-filter span never exceeds the gain the peak needs.
    Output is delayed internally but emitted aligned with input sample positions.
    """

    def __init__(self, sample_rate: int, ceiling_db: float, lookahead_ms: float = 5.0):
        self.ceiling = float(db_to_gain(ceiling_db))
        self.lookahead = max(int(sample_rate * lookahead_ms / 1000), 1)
        self.pad = max(4 * self.lookahead, 2048)
        self.buffer = np.zeros((0, 2))
        self.emitted = 0  # samples of self.buffer already emitted

    def _gains(self, data: np.ndarray) -> np.ndarray:
        peaks = true_peak_per_sample(data)
        required = np.minimum(1.0, self.ceiling / np.maximum(peaks, 1e-12))
        held = minimum_filter1d(required, size=2 * self.lookahead + 1, mode="nearest")
        smoothed = uniform_filter1d(held, size=self.lookahead + 1, mode="nearest")
        return np.minimum(smoothed, required)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        self.buffer = np.concatenate([self.buffer, block])
        emit_end = len(self.buffer) - self.pad
        if emit_end <= self.emitted:
            return np.zeros((0, 2))
        gains = self._gains(self.buffer)
        out = self.buffer[self.emitted : emit_end] * gains[self.emitted : emit_end, None]
        keep_from = max(emit_end - self.pad, 0)
        self.buffer = self.buffer[keep_from:]
        self.emitted = emit_end - keep_from
        return out

    def flush(self) -> np.ndarray:
        if len(self.buffer) <= self.emitted:
            return np.zeros((0, 2))
        gains = self._gains(self.buffer)
        out = self.buffer[self.emitted :] * gains[self.emitted :, None]
        self.buffer = np.zeros((0, 2))
        self.emitted = 0
        return out


def fade_gains(start: int, count: int, total: int, fade_in: int, fade_out: int) -> np.ndarray:
    """Equal-power fade gains for samples [start, start+count) of a `total`-sample program."""
    index = np.arange(start, start + count, dtype=np.float64)
    gains = np.ones(count)
    if fade_in > 0:
        gains *= np.sin(np.clip(index / fade_in, 0, 1) * math.pi / 2)
    if fade_out > 0:
        gains *= np.sin(np.clip((total - 1 - index) / fade_out, 0, 1) * math.pi / 2)
    return gains
