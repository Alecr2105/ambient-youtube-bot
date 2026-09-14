from __future__ import annotations

import numpy as np

from app.audio.generators.base import Generator, NoiseSource


class ColoredNoise(Generator):
    color: str

    def setup(self, width: float = 1.0) -> None:
        self.noise = NoiseSource(self.rng, self.sample_rate, self.color, width)

    def generate(self, frames: int) -> np.ndarray:
        return self.noise(frames)


class WhiteNoise(ColoredNoise):
    name = "white_noise"
    color = "white"


class PinkNoise(ColoredNoise):
    name = "pink_noise"
    color = "pink"


class BrownNoise(ColoredNoise):
    name = "brown_noise"
    color = "brown"
