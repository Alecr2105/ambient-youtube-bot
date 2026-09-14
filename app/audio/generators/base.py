from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import numpy as np
from scipy import signal

from app.audio.processor.filters import SosFilter, butter_sos, resonator_sos

NOMINAL_RMS = 0.1
CALIBRATION_SECONDS = 10.0

# Paul Kellet / RBJ 3-pole pink noise approximation (-3 dB/oct, ±0.3 dB above 10 Hz).
PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])


class NoiseSource:
    """Stereo white/pink/brown noise with adjustable inter-channel correlation."""

    def __init__(self, rng: np.random.Generator, sample_rate: int, color: str = "white", width: float = 1.0):
        if color not in ("white", "pink", "brown"):
            raise ValueError(f"unknown noise color {color}")
        self.rng = rng
        self.color = color
        self.width = float(np.clip(width, 0.0, 1.0))
        self.zi_pink = np.zeros((3, 2))
        self.zi_brown = np.zeros((1, 2))
        self.brown_hp = SosFilter(butter_sos(sample_rate, "highpass", 20.0, 1))

    def __call__(self, frames: int) -> np.ndarray:
        raw = self.rng.standard_normal((frames, 2))
        mid = (raw[:, 0] + raw[:, 1]) / math.sqrt(2)
        side = (raw[:, 0] - raw[:, 1]) / math.sqrt(2) * self.width
        norm = math.sqrt(2 / (1 + self.width**2))
        white = np.column_stack([mid + side, mid - side]) / math.sqrt(2) * norm
        if self.color == "white":
            return white
        if self.color == "pink":
            out, self.zi_pink = signal.lfilter(PINK_B, PINK_A, white, axis=0, zi=self.zi_pink)
            return out * 8.0
        out, self.zi_brown = signal.lfilter([1.0], [1.0, -0.998], white, axis=0, zi=self.zi_brown)
        return self.brown_hp(out) * 0.08


class SmoothRandom:
    """Aperiodic control signal in [-1, 1]: random breakpoints at random intervals, cosine-interpolated.

    Breakpoints are precomputed for the whole program, so values depend only on the sample index.
    """

    def __init__(self, rng: np.random.Generator, sample_rate: int, duration_s: float, min_period_s: float, max_period_s: float):
        if min_period_s <= 0 or max_period_s < min_period_s:
            raise ValueError("invalid SmoothRandom periods")
        mean_period = (min_period_s + max_period_s) / 2
        count = int(duration_s / mean_period * 1.3) + 8
        intervals = rng.uniform(min_period_s, max_period_s, count)
        times = np.concatenate([[0.0], np.cumsum(intervals)])
        while times[-1] < duration_s + max_period_s:
            times = np.append(times, times[-1] + rng.uniform(min_period_s, max_period_s))
        self.positions = times * sample_rate
        self.values = rng.uniform(-1.0, 1.0, len(times))

    def at(self, start: int, frames: int) -> np.ndarray:
        index = np.arange(start, start + frames, dtype=np.float64)
        seg = np.clip(np.searchsorted(self.positions, index, side="right") - 1, 0, len(self.positions) - 2)
        left, right = self.positions[seg], self.positions[seg + 1]
        t = (index - left) / (right - left)
        blend = (1 - np.cos(np.pi * t)) / 2
        return self.values[seg] + (self.values[seg + 1] - self.values[seg]) * blend

    def at_seconds(self, seconds: np.ndarray, sample_rate: int) -> np.ndarray:
        index = np.asarray(seconds) * sample_rate
        seg = np.clip(np.searchsorted(self.positions, index, side="right") - 1, 0, len(self.positions) - 2)
        t = (index - self.positions[seg]) / (self.positions[seg + 1] - self.positions[seg])
        blend = (1 - np.cos(np.pi * t)) / 2
        return self.values[seg] + (self.values[seg + 1] - self.values[seg]) * blend


class ImpulseBank:
    """Poisson impulses routed to a bank of resonators (drops, crackles, taps, bubbles)."""

    def __init__(
        self,
        rng: np.random.Generator,
        sample_rate: int,
        freqs: np.ndarray,
        q: np.ndarray | float,
        pareto_shape: float = 2.5,
        width: float = 1.0,
    ):
        self.rng = rng
        self.sample_rate = sample_rate
        qs = np.broadcast_to(np.asarray(q, dtype=float), freqs.shape)
        self.filters = [SosFilter(resonator_sos(sample_rate, f, qv)) for f, qv in zip(freqs, qs, strict=True)]
        self.pareto_shape = pareto_shape
        self.width = width

    def __call__(self, frames: int, rate_hz: np.ndarray | float) -> np.ndarray:
        rate = np.broadcast_to(np.asarray(rate_hz, dtype=float), (frames,))
        expected = rate / self.sample_rate
        count = self.rng.poisson(float(expected.sum()))
        out = np.zeros((frames, 2))
        if count:
            cdf = np.cumsum(expected)
            positions = np.minimum(np.searchsorted(cdf, self.rng.uniform(0, cdf[-1], count)), frames - 1)
            amps = (self.rng.pareto(self.pareto_shape, count) + 1) * self.rng.choice([-1.0, 1.0], count)
            angle = (self.rng.uniform(-self.width, self.width, count) + 1) * math.pi / 4
            which = self.rng.integers(0, len(self.filters), count)
        excitation = np.zeros((frames, 2))
        for idx, filt in enumerate(self.filters):
            # Filters run even without impulses so their ringing state continues across blocks.
            excitation.fill(0.0)
            if count:
                mask = which == idx
                np.add.at(excitation[:, 0], positions[mask], amps[mask] * np.cos(angle[mask]))
                np.add.at(excitation[:, 1], positions[mask], amps[mask] * np.sin(angle[mask]))
            out += filt(excitation)
        return out


class Generator(ABC):
    """Continuous layer source. Output is stereo float64, scaled to roughly NOMINAL_RMS."""

    name: ClassVar[str]

    def __init__(self, sample_rate: int, rng: np.random.Generator, duration_s: float, **params: Any):
        self.sample_rate = sample_rate
        self.rng = rng
        self.duration_s = duration_s
        self.position = 0
        self.scale = 1.0
        self.setup(**params)

    @abstractmethod
    def setup(self, **params: Any) -> None: ...

    @abstractmethod
    def generate(self, frames: int) -> np.ndarray: ...

    def render(self, frames: int) -> np.ndarray:
        block = self.generate(frames) * self.scale
        self.position += frames
        return block

    @classmethod
    def create(cls, sample_rate: int, seed: np.random.SeedSequence, duration_s: float, **params: Any) -> Generator:
        calibration_seed, run_seed = seed.spawn(2)
        probe = cls(sample_rate, np.random.default_rng(calibration_seed), CALIBRATION_SECONDS + 1, **params)
        probe_frames = int(CALIBRATION_SECONDS * sample_rate)
        rms = float(np.sqrt(np.mean(np.square(probe.render(probe_frames)))))
        instance = cls(sample_rate, np.random.default_rng(run_seed), duration_s, **params)
        instance.scale = NOMINAL_RMS / rms if rms > 1e-9 else 1.0
        return instance


class EventGenerator(ABC):
    """One-shot sound (thunder, gust, ...). Returns a stereo buffer with peak near 1.0."""

    name: ClassVar[str]

    def __init__(self, sample_rate: int, rng: np.random.Generator, **params: Any):
        self.sample_rate = sample_rate
        self.rng = rng
        self.setup(**params)

    @abstractmethod
    def setup(self, **params: Any) -> None: ...

    @abstractmethod
    def generate(self) -> np.ndarray: ...

    def render(self) -> np.ndarray:
        event = self.generate()
        peak = np.max(np.abs(event))
        return event / peak if peak > 0 else event
