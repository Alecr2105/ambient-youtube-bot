from __future__ import annotations

import numpy as np

from app.audio.generators.base import Generator, NoiseSource, SmoothRandom
from app.audio.processor.filters import SosFilter, butter_sos, resonator_sos

BANDS = ((120, 400), (400, 1200), (1200, 3500))


class Wind(Generator):
    """Pink noise through three bands whose balance drifts, shaped by slow aperiodic gusts."""

    name = "wind"

    def setup(self, gustiness: float = 0.5, whistle_level: float = 0.1, min_gust_s: float = 5.0, max_gust_s: float = 25.0) -> None:
        sr = self.sample_rate
        self.noise = NoiseSource(self.rng, sr, "pink", width=0.85)
        self.bands = [SosFilter(butter_sos(sr, "bandpass", band, 2)) for band in BANDS]
        self.band_mods = [SmoothRandom(self.rng, sr, self.duration_s, 2.0, 10.0) for _ in BANDS]
        self.gust = SmoothRandom(self.rng, sr, self.duration_s, min_gust_s, max_gust_s)
        self.whistle_noise = NoiseSource(self.rng, sr, "white", width=0.5)
        self.whistle = SosFilter(resonator_sos(sr, float(self.rng.uniform(600, 1400)), 35.0))
        self.gustiness = gustiness
        self.whistle_level = whistle_level

    def generate(self, frames: int) -> np.ndarray:
        gust = (self.gust.at(self.position, frames) + 1) / 2
        envelope = (1 - self.gustiness) + self.gustiness * gust**1.5
        noise = self.noise(frames)
        out = np.zeros((frames, 2))
        for band, mod, weight in zip(self.bands, self.band_mods, (1.0, 0.8, 0.45), strict=True):
            gain = weight * (1 + 0.4 * mod.at(self.position, frames)) * (0.6 + 0.4 * gust)
            out += band(noise) * gain[:, None]
        whistle = self.whistle(self.whistle_noise(frames)) * self.whistle_level * 3 * (gust**3)[:, None]
        return (out + whistle) * envelope[:, None]
