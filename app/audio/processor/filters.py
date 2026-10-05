from __future__ import annotations

import math

import numpy as np
from scipy import signal


def biquad_high_shelf(sample_rate: int, freq: float, gain_db: float, q: float = 0.7071) -> np.ndarray:
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * freq / sample_rate
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)
    sqrt_a = math.sqrt(a)
    b0 = a * ((a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha)
    b1 = -2 * a * ((a - 1) + (a + 1) * cos_w0)
    b2 = a * ((a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    a0 = (a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha
    a1 = 2 * ((a - 1) - (a + 1) * cos_w0)
    a2 = (a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def biquad_low_shelf(sample_rate: int, freq: float, gain_db: float, q: float = 0.7071) -> np.ndarray:
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * freq / sample_rate
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)
    sqrt_a = math.sqrt(a)
    b0 = a * ((a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha)
    b1 = 2 * a * ((a - 1) - (a + 1) * cos_w0)
    b2 = a * ((a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    a0 = (a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha
    a1 = -2 * ((a - 1) + (a + 1) * cos_w0)
    a2 = (a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def biquad_high_pass(sample_rate: int, freq: float, q: float) -> np.ndarray:
    w0 = 2 * math.pi * freq / sample_rate
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)
    a0 = 1 + alpha
    return np.array([[(1 + cos_w0) / 2 / a0, -(1 + cos_w0) / a0, (1 + cos_w0) / 2 / a0, 1.0, -2 * cos_w0 / a0, (1 - alpha) / a0]])


def butter_sos(sample_rate: int, kind: str, freq: float | tuple[float, float], order: int = 2) -> np.ndarray:
    nyquist = sample_rate / 2
    if isinstance(freq, tuple):
        low, high = (min(max(f, 1.0), nyquist * 0.98) for f in freq)
        return signal.butter(order, [low, high], btype=kind, fs=sample_rate, output="sos")
    return signal.butter(order, min(max(freq, 1.0), nyquist * 0.98), btype=kind, fs=sample_rate, output="sos")


class SosFilter:
    """Stateful multi-channel SOS filter; output is independent of block size."""

    def __init__(self, sos: np.ndarray, channels: int = 2):
        self.sos = np.atleast_2d(sos)
        self.zi = np.zeros((self.sos.shape[0], 2, channels))

    def __call__(self, block: np.ndarray) -> np.ndarray:
        out, self.zi = signal.sosfilt(self.sos, block, axis=0, zi=self.zi)
        return out


class FilterChain:
    def __init__(self, filters: list[SosFilter] | None = None):
        self.filters = filters or []

    def __call__(self, block: np.ndarray) -> np.ndarray:
        for f in self.filters:
            block = f(block)
        return block
