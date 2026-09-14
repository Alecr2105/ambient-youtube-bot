from __future__ import annotations

import numpy as np

from app.audio.generators.base import Generator, ImpulseBank, NoiseSource, SmoothRandom
from app.audio.processor.filters import FilterChain, SosFilter, butter_sos


class Rain(Generator):
    """Open-air rain: filtered noise bed + stochastic drops on resonant surfaces (leaves, ground)."""

    name = "rain"

    def setup(
        self,
        drops_per_second: float = 1200.0,
        brightness: float = 0.5,
        bed_level: float = 0.6,
        drop_level: float = 0.5,
        variation_period_s: float = 20.0,
    ) -> None:
        sr = self.sample_rate
        low = 250 + 350 * brightness
        high = 6000 + 6000 * brightness
        self.bed = NoiseSource(self.rng, sr, "pink", width=0.9)
        self.bed_filter = FilterChain([SosFilter(butter_sos(sr, "bandpass", (low, high), 2))])
        self.hiss = NoiseSource(self.rng, sr, "white", width=1.0)
        self.hiss_filter = SosFilter(butter_sos(sr, "highpass", 5000 + 3000 * brightness, 2))
        freqs = np.exp(self.rng.uniform(np.log(1200 + 1000 * brightness), np.log(5000 + 3000 * brightness), 10))
        self.drops = ImpulseBank(self.rng, sr, freqs, self.rng.uniform(1.5, 4.0, 10), pareto_shape=3.0)
        self.density_mod = SmoothRandom(self.rng, sr, self.duration_s, variation_period_s * 0.3, variation_period_s)
        self.hiss_mod = SmoothRandom(self.rng, sr, self.duration_s, 0.2, 1.5)
        self.rate = drops_per_second
        self.bed_level = bed_level
        self.drop_level = drop_level

    def generate(self, frames: int) -> np.ndarray:
        density = 1 + 0.25 * self.density_mod.at(self.position, frames)
        bed = self.bed_filter(self.bed(frames)) * self.bed_level * density[:, None]
        hiss = self.hiss_filter(self.hiss(frames)) * 0.15 * (1 + 0.2 * self.hiss_mod.at(self.position, frames))[:, None]
        drops = self.drops(frames, self.rate * density) * self.drop_level * 0.02
        return bed + hiss + drops


class RainOnWindow(Generator):
    """Rain heard indoors: muffled outside rain, glassy taps and slow rivulets on the pane."""

    name = "rain_window"

    def setup(
        self,
        taps_per_second: float = 180.0,
        tap_level: float = 0.6,
        outside_level: float = 0.7,
        rivulet_level: float = 0.25,
    ) -> None:
        sr = self.sample_rate
        self.outside = NoiseSource(self.rng, sr, "pink", width=0.8)
        self.outside_filter = FilterChain(
            [SosFilter(butter_sos(sr, "highpass", 90, 2)), SosFilter(butter_sos(sr, "lowpass", 2600, 2))]
        )
        freqs = np.exp(self.rng.uniform(np.log(700), np.log(3200), 9))
        self.taps = ImpulseBank(self.rng, sr, freqs, self.rng.uniform(6.0, 14.0, 9), pareto_shape=2.2, width=0.9)
        self.thumps = ImpulseBank(self.rng, sr, self.rng.uniform(140, 320, 3), 3.0, pareto_shape=3.5, width=0.6)
        self.rivulet = NoiseSource(self.rng, sr, "white", width=0.6)
        self.rivulet_filter = SosFilter(butter_sos(sr, "bandpass", (1200, 4200), 2))
        self.rivulet_mod = SmoothRandom(self.rng, sr, self.duration_s, 0.4, 3.0)
        self.tap_mod = SmoothRandom(self.rng, sr, self.duration_s, 4.0, 25.0)
        self.rate = taps_per_second
        self.tap_level = tap_level
        self.outside_level = outside_level
        self.rivulet_level = rivulet_level

    def generate(self, frames: int) -> np.ndarray:
        intensity = 1 + 0.35 * self.tap_mod.at(self.position, frames)
        outside = self.outside_filter(self.outside(frames)) * self.outside_level
        taps = self.taps(frames, self.rate * intensity) * self.tap_level * 0.03
        thumps = self.thumps(frames, self.rate * 0.04 * intensity) * self.tap_level * 0.02
        rivulet_env = np.clip(self.rivulet_mod.at(self.position, frames), 0, 1) ** 2
        rivulet = self.rivulet_filter(self.rivulet(frames)) * self.rivulet_level * rivulet_env[:, None]
        return outside + taps + thumps + rivulet


class RainOnRoof(Generator):
    """Rain on a metal (zinc) roof: inharmonic metallic pings over a dense drumming bed."""

    name = "rain_roof"

    def setup(self, drops_per_second: float = 700.0, metal_level: float = 0.6, bed_level: float = 0.6, boom_level: float = 0.3) -> None:
        sr = self.sample_rate
        base = np.exp(self.rng.uniform(np.log(900), np.log(2600), 4))
        # Plate modes are inharmonic; a few ratios per base frequency give the tinny zinc colour.
        freqs = np.concatenate([base, base * 2.76, base * 5.40])
        self.metal = ImpulseBank(self.rng, sr, freqs, self.rng.uniform(18.0, 40.0, len(freqs)), pareto_shape=2.8)
        self.bed = NoiseSource(self.rng, sr, "pink", width=0.9)
        self.bed_filter = SosFilter(butter_sos(sr, "bandpass", (300, 9000), 2))
        self.boom = NoiseSource(self.rng, sr, "brown", width=0.4)
        self.boom_filter = SosFilter(butter_sos(sr, "bandpass", (70, 220), 2))
        self.mod = SmoothRandom(self.rng, sr, self.duration_s, 5.0, 30.0)
        self.rate = drops_per_second
        self.metal_level = metal_level
        self.bed_level = bed_level
        self.boom_level = boom_level

    def generate(self, frames: int) -> np.ndarray:
        intensity = 1 + 0.3 * self.mod.at(self.position, frames)
        metal = self.metal(frames, self.rate * intensity) * self.metal_level * 0.015
        bed = self.bed_filter(self.bed(frames)) * self.bed_level * intensity[:, None]
        boom = self.boom_filter(self.boom(frames)) * self.boom_level * intensity[:, None]
        return metal + bed + boom
