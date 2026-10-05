from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import numpy as np

NOMINAL_RMS = 0.1
CALIBRATION_SECONDS = 10.0


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
