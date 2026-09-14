from __future__ import annotations

import numpy as np
from scipy import signal

from app.audio.generators.base import EventGenerator, NoiseSource
from app.audio.processor.filters import butter_sos


class Thunder(EventGenerator):
    """A single thunder roll. `distance` 0 = overhead (sharp crack), 1 = far (dull rumble)."""

    name = "thunder"

    def setup(self, min_distance: float = 0.3, max_distance: float = 1.0) -> None:
        self.distance = float(self.rng.uniform(min_distance, max_distance))

    def _burst_envelope(self, frames: int, onset: int, attack: int, decay: float) -> np.ndarray:
        t = np.arange(frames) - onset
        env = np.zeros(frames)
        rising = (t >= 0) & (t < attack)
        env[rising] = t[rising] / max(attack, 1)
        after = t >= attack
        env[after] = np.exp(-(t[after] - attack) / decay)
        return env

    def generate(self) -> np.ndarray:
        sr, rng, d = self.sample_rate, self.rng, self.distance
        duration = rng.uniform(7, 12) + d * rng.uniform(4, 12)
        frames = int(sr * duration)

        rumble_env = np.zeros(frames)
        for _ in range(int(rng.integers(3, 9))):
            onset = int(rng.uniform(0, 0.55) * frames)
            attack = int(sr * rng.uniform(0.05, 0.4 + 0.6 * d))
            decay = sr * rng.uniform(0.8, 2.5 + 3 * d)
            rumble_env += rng.uniform(0.3, 1.0) * self._burst_envelope(frames, onset, attack, decay)

        rumble = NoiseSource(rng, sr, "brown", width=0.9)(frames)
        cutoff = 450 - 300 * d
        rumble = signal.sosfilt(butter_sos(sr, "lowpass", cutoff, 4), rumble, axis=0) * rumble_env[:, None]

        grumble = NoiseSource(rng, sr, "pink", width=0.9)(frames)
        grumble = signal.sosfilt(butter_sos(sr, "bandpass", (140, 1300 - 800 * d), 2), grumble, axis=0)
        grumble *= (rumble_env * (0.5 - 0.35 * d))[:, None]

        event = rumble * 6 + grumble
        if d < 0.45:
            crack_env = self._burst_envelope(frames, int(sr * rng.uniform(0.0, 0.15)), int(sr * 0.01), sr * rng.uniform(0.15, 0.5))
            crack = NoiseSource(rng, sr, "white", width=1.0)(frames)
            crack = signal.sosfilt(butter_sos(sr, "highpass", 900, 2), crack, axis=0) * crack_env[:, None]
            event += crack * (0.45 - d) * 2.5

        tail = int(sr * 1.5)
        event[-tail:] *= np.linspace(1, 0, tail)[:, None]
        return event
