from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.audio.generators.base import Generator, ImpulseBank, NoiseSource, SmoothRandom
from app.audio.processor.filters import FilterChain, SosFilter, butter_sos


@dataclass
class Wave:
    start: int
    rise: int
    decay: int
    amplitude: float
    pan: float

    def envelope(self, index: np.ndarray) -> np.ndarray:
        t = (index - self.start).astype(np.float64)
        env = np.zeros(len(index))
        rising = (t >= 0) & (t < self.rise)
        env[rising] = (1 - np.cos(np.pi * t[rising] / self.rise)) / 2
        falling = (t >= self.rise) & (t < self.rise + self.decay)
        env[falling] = (1 - (t[falling] - self.rise) / self.decay) ** 2
        return env * self.amplitude

    @property
    def end(self) -> int:
        return self.start + self.rise + self.decay


class OceanWaves(Generator):
    """Irregular swells: a low body rising and breaking, then a brighter wash that trails behind."""

    name = "ocean"

    def setup(self, min_period_s: float = 8.0, max_period_s: float = 14.0, wash_level: float = 0.6, surf_level: float = 0.25) -> None:
        sr = self.sample_rate
        self.body = NoiseSource(self.rng, sr, "brown", width=0.7)
        self.body_filter = SosFilter(butter_sos(sr, "lowpass", float(self.rng.uniform(450, 750)), 2))
        self.wash = NoiseSource(self.rng, sr, "pink", width=0.95)
        self.wash_filter = SosFilter(butter_sos(sr, "bandpass", (700, 9000), 2))
        self.surf = NoiseSource(self.rng, sr, "brown", width=0.9)
        self.surf_filter = SosFilter(butter_sos(sr, "lowpass", 300, 2))
        self.min_period = min_period_s
        self.max_period = max_period_s
        self.wash_level = wash_level
        self.surf_level = surf_level
        self.wash_delay = int(sr * self.rng.uniform(1.2, 2.5))
        self.waves: list[Wave] = []
        self.next_start = 0

    def _schedule(self, until: int) -> None:
        sr = self.sample_rate
        while self.next_start < until:
            rise = int(sr * self.rng.uniform(1.8, 4.0))
            decay = int(sr * self.rng.uniform(4.0, 9.0))
            amplitude = float(self.rng.uniform(0.45, 1.0))
            self.waves.append(Wave(self.next_start, rise, decay, amplitude, float(self.rng.uniform(-0.5, 0.5))))
            period = self.rng.uniform(self.min_period, self.max_period)
            # Occasional sets of closer waves break the regularity.
            if self.rng.random() < 0.15:
                period *= self.rng.uniform(0.5, 0.75)
            self.next_start += int(sr * period)

    def generate(self, frames: int) -> np.ndarray:
        start, end = self.position, self.position + frames
        self._schedule(end + self.wash_delay)
        index = np.arange(start, end)
        body_env = np.zeros((frames, 2))
        wash_env = np.zeros((frames, 2))
        for wave in self.waves:
            if wave.start > end or wave.end + self.wash_delay < start:
                continue
            angle = (wave.pan + 1) * math.pi / 4
            gains = np.array([math.cos(angle), math.sin(angle)]) * math.sqrt(2)
            body_env += (wave.envelope(index) ** 0.8)[:, None] * gains
            wash_env += (wave.envelope(index - self.wash_delay) ** 1.6)[:, None] * gains
        self.waves = [w for w in self.waves if w.end + self.wash_delay >= end]
        body = self.body_filter(self.body(frames)) * body_env
        wash = self.wash_filter(self.wash(frames)) * wash_env * self.wash_level
        surf = self.surf_filter(self.surf(frames)) * self.surf_level
        return body + wash + surf


class River(Generator):
    """Broadband flowing water with quick shimmer and small resonant bubbles."""

    name = "river"

    def setup(self, flow: float = 0.6, bubbles_per_second: float = 60.0, bubble_level: float = 0.5, rumble_level: float = 0.3) -> None:
        sr = self.sample_rate
        self.flow_noise = NoiseSource(self.rng, sr, "pink", width=0.9)
        self.flow_filter = SosFilter(butter_sos(sr, "bandpass", (150 + 150 * flow, 5000 + 3000 * flow), 2))
        self.shimmer = SmoothRandom(self.rng, sr, self.duration_s, 0.08, 0.5)
        self.surge = SmoothRandom(self.rng, sr, self.duration_s, 6.0, 40.0)
        freqs = np.exp(self.rng.uniform(np.log(300), np.log(2200), 10))
        self.bubbles = ImpulseBank(self.rng, sr, freqs, self.rng.uniform(8.0, 22.0, 10), pareto_shape=2.6)
        self.rumble = NoiseSource(self.rng, sr, "brown", width=0.6)
        self.rumble_filter = SosFilter(butter_sos(sr, "lowpass", 220, 2))
        self.rate = bubbles_per_second
        self.bubble_level = bubble_level
        self.rumble_level = rumble_level

    def generate(self, frames: int) -> np.ndarray:
        surge = 1 + 0.2 * self.surge.at(self.position, frames)
        shimmer = 1 + 0.12 * self.shimmer.at(self.position, frames)
        flow = self.flow_filter(self.flow_noise(frames)) * (surge * shimmer)[:, None]
        bubbles = self.bubbles(frames, self.rate * surge) * self.bubble_level * 0.02
        rumble = self.rumble_filter(self.rumble(frames)) * self.rumble_level
        return flow + bubbles + rumble


class Waterfall(Generator):
    """Dense, steady broadband roar with a strong low body and fine spray."""

    name = "waterfall"

    def setup(self, roar_level: float = 1.0, body_level: float = 0.6, spray_level: float = 0.3) -> None:
        sr = self.sample_rate
        self.roar = NoiseSource(self.rng, sr, "pink", width=0.95)
        self.roar_filter = FilterChain([SosFilter(butter_sos(sr, "highpass", 80, 2)), SosFilter(butter_sos(sr, "lowpass", 8500, 2))])
        self.body = NoiseSource(self.rng, sr, "brown", width=0.8)
        self.body_filter = SosFilter(butter_sos(sr, "lowpass", 160, 2))
        self.spray = NoiseSource(self.rng, sr, "white", width=1.0)
        self.spray_filter = SosFilter(butter_sos(sr, "highpass", 6000, 2))
        self.drift = SmoothRandom(self.rng, sr, self.duration_s, 10.0, 60.0)
        self.roar_level = roar_level
        self.body_level = body_level
        self.spray_level = spray_level

    def generate(self, frames: int) -> np.ndarray:
        drift = (1 + 0.08 * self.drift.at(self.position, frames))[:, None]
        roar = self.roar_filter(self.roar(frames)) * self.roar_level
        body = self.body_filter(self.body(frames)) * self.body_level
        spray = self.spray_filter(self.spray(frames)) * self.spray_level
        return (roar + body + spray) * drift
