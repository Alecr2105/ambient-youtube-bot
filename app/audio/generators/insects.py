from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.audio.generators.base import Generator, NoiseSource, SmoothRandom
from app.audio.processor.filters import SosFilter, butter_sos


@dataclass
class Cricket:
    freq: float
    pulse: int
    pulses_per_chirp: int
    pulse_gap: int
    chirp_period: float
    jitter: float
    level: float
    gains: np.ndarray
    next_chirp: int
    activity: SmoothRandom


class Crickets(Generator):
    """Several individual crickets (pulse-train chirps, each with its own pitch, rhythm and position)
    over a faint distant chorus. Chirp timing jitters so no two cycles line up."""

    name = "crickets"

    def setup(self, individuals: int = 6, chorus_level: float = 0.3) -> None:
        sr = self.sample_rate
        self.crickets = []
        for _ in range(int(individuals)):
            pan = self.rng.uniform(-0.9, 0.9)
            angle = (pan + 1) * math.pi / 4
            self.crickets.append(
                Cricket(
                    freq=float(self.rng.uniform(3800, 5300)),
                    pulse=int(sr * self.rng.uniform(0.010, 0.020)),
                    pulses_per_chirp=int(self.rng.integers(2, 6)),
                    pulse_gap=int(sr * self.rng.uniform(0.025, 0.045)),
                    chirp_period=float(self.rng.uniform(0.35, 1.2)),
                    jitter=float(self.rng.uniform(0.08, 0.2)),
                    level=float(self.rng.lognormal(0, 0.4)),
                    gains=np.array([math.cos(angle), math.sin(angle)]),
                    next_chirp=int(sr * self.rng.uniform(0, 1.0)),
                    # Individuals fall silent and resume, like real insects.
                    activity=SmoothRandom(self.rng, sr, self.duration_s, 8.0, 60.0),
                )
            )
        self.chorus = NoiseSource(self.rng, sr, "white", width=1.0)
        self.chorus_filter = SosFilter(butter_sos(sr, "bandpass", (4000, 5200), 2))
        self.chorus_mod = SmoothRandom(self.rng, sr, self.duration_s, 0.02, 0.05)
        self.chorus_level = chorus_level

    def generate(self, frames: int) -> np.ndarray:
        sr = self.sample_rate
        start, end = self.position, self.position + frames
        out = np.zeros((frames, 2))
        for c in self.crickets:
            envelope = np.zeros(frames)
            while c.next_chirp < end:
                for p in range(c.pulses_per_chirp):
                    p_start = c.next_chirp + p * c.pulse_gap
                    lo, hi = max(p_start, start), min(p_start + c.pulse, end)
                    if lo < hi:
                        t = (np.arange(lo, hi) - p_start) / c.pulse
                        envelope[lo - start : hi - start] += np.sin(np.pi * t) ** 2
                chirp_end = c.next_chirp + c.pulses_per_chirp * c.pulse_gap
                if chirp_end > end:
                    break
                c.next_chirp += int(sr * c.chirp_period * (1 + self.rng.uniform(-c.jitter, c.jitter)))
            if not envelope.any():
                continue
            activity = np.clip(c.activity.at(start, frames) * 1.5 + 0.5, 0, 1)
            carrier = np.sin(2 * np.pi * c.freq * np.arange(start, end) / sr)
            out += (carrier * envelope * activity * c.level)[:, None] * c.gains
        chorus_env = 1 + 0.5 * self.chorus_mod.at(start, frames)
        chorus = self.chorus_filter(self.chorus(frames)) * self.chorus_level * chorus_env[:, None]
        return out + chorus
