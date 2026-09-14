from __future__ import annotations

import numpy as np

from app.audio.generators.base import Generator, ImpulseBank, NoiseSource, SmoothRandom
from app.audio.processor.filters import SosFilter, butter_sos


class Fireplace(Generator):
    """Low rumble and breathing flame roar, with clustered crackles and occasional pops."""

    name = "fire"

    def setup(self, crackles_per_second: float = 18.0, pops_per_minute: float = 8.0, crackle_level: float = 0.7, roar_level: float = 0.4) -> None:
        sr = self.sample_rate
        self.rumble = NoiseSource(self.rng, sr, "brown", width=0.5)
        self.rumble_filter = SosFilter(butter_sos(sr, "lowpass", 250, 2))
        self.roar = NoiseSource(self.rng, sr, "pink", width=0.6)
        self.roar_filter = SosFilter(butter_sos(sr, "bandpass", (300, 1500), 2))
        self.breath = SmoothRandom(self.rng, sr, self.duration_s, 0.5, 3.0)
        self.cluster = SmoothRandom(self.rng, sr, self.duration_s, 0.3, 2.5)
        crackle_freqs = np.exp(self.rng.uniform(np.log(1500), np.log(6500), 8))
        self.crackles = ImpulseBank(self.rng, sr, crackle_freqs, self.rng.uniform(0.9, 2.5, 8), pareto_shape=1.8, width=0.7)
        self.pops = ImpulseBank(self.rng, sr, self.rng.uniform(500, 1400, 4), 3.0, pareto_shape=3.0, width=0.5)
        self.crackle_rate = crackles_per_second
        self.pop_rate = pops_per_minute / 60
        self.crackle_level = crackle_level
        self.roar_level = roar_level

    def generate(self, frames: int) -> np.ndarray:
        breath = 1 + 0.35 * self.breath.at(self.position, frames)
        # Squaring the cluster signal makes crackles bunch up instead of arriving evenly.
        cluster = (self.cluster.at(self.position, frames) + 1) ** 2
        rumble = self.rumble_filter(self.rumble(frames)) * breath[:, None]
        roar = self.roar_filter(self.roar(frames)) * self.roar_level * breath[:, None]
        crackles = self.crackles(frames, self.crackle_rate * cluster) * self.crackle_level * 0.05
        pops = self.pops(frames, self.pop_rate) * 0.12
        return rumble + roar + crackles + pops
