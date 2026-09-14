from __future__ import annotations

import math
from collections import deque
from fractions import Fraction
from functools import lru_cache

import numpy as np
import soundfile as sf
from scipy import signal

from app.audio.generators.base import EventGenerator, Generator

MAX_PLACEMENT_TRIES = 12


@lru_cache(maxsize=48)
def _load(path: str, sample_rate: int) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != sample_rate:
        raise ValueError(f"{path}: expected {sample_rate} Hz (catalog files are normalized), got {rate}")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    audio.setflags(write=False)
    return audio


def _repitch(segment: np.ndarray, cents: float) -> np.ndarray:
    if abs(cents) < 0.5:
        return segment
    # Playing faster raises pitch: output length = input length / factor.
    ratio = Fraction(1 / 2 ** (cents / 1200)).limit_denominator(200)
    return signal.resample_poly(segment, ratio.numerator, ratio.denominator, axis=0)


class GranularTexture(Generator):
    """Rebuilds hours of texture from a few recordings.

    Grains of random length are cut at random offsets, re-pitched by a few cents, re-levelled,
    occasionally reversed, and joined with long equal-power crossfades. Grains avoid reusing
    the regions of recent grains, so the output never repeats a stretch of source in sequence.
    """

    name = "granular"

    def setup(
        self,
        sources: list[str],
        grain_min_s: float = 6.0,
        grain_max_s: float = 25.0,
        crossfade_s: float = 4.0,
        pitch_cents: float = 30.0,
        gain_jitter_db: float = 1.5,
        reverse_probability: float = 0.0,
    ) -> None:
        sr = self.sample_rate
        self.crossfade = int(crossfade_s * sr)
        self.grain_min = int(grain_min_s * sr)
        self.grain_max = int(max(grain_max_s, grain_min_s) * sr)
        margin = int(self.crossfade * 2 * 1.05) + self.grain_min
        self.sources = []
        for path in sources:
            audio = _load(str(path), sr)
            if len(audio) < margin:
                continue
            rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
            if rms > 1e-6:
                self.sources.append((audio, 0.1 / rms))
        if not self.sources:
            raise ValueError("no source long enough for granular synthesis")
        self.pitch_cents = pitch_cents
        self.gain_jitter_db = gain_jitter_db
        self.reverse_probability = reverse_probability
        self.recent: deque[tuple[int, int, int]] = deque(maxlen=max(4, 3 * len(self.sources)))
        self.buffer = np.zeros((0, 2))
        self.buffer_start = 0
        self.junctions: list[int] = []
        t = np.linspace(0, math.pi / 2, self.crossfade)
        self.fade_out, self.fade_in = np.cos(t)[:, None], np.sin(t)[:, None]

    def _overlaps(self, source: int, start: int, end: int) -> bool:
        return any(s == source and start < e and b < end for s, b, e in self.recent)

    def _grain(self) -> np.ndarray:
        rng = self.rng
        length = int(rng.integers(self.grain_min, self.grain_max + 1)) + self.crossfade
        cents = float(rng.uniform(-self.pitch_cents, self.pitch_cents))
        needed = int(math.ceil(length * 2 ** (abs(cents) / 1200))) + 64
        for _ in range(MAX_PLACEMENT_TRIES):
            index = int(rng.integers(len(self.sources)))
            audio, scale = self.sources[index]
            span = min(needed, len(audio))
            start = int(rng.integers(0, len(audio) - span + 1))
            if not self._overlaps(index, start, start + span):
                break
        self.recent.append((index, start, start + span))
        grain = _repitch(np.asarray(audio[start : start + span], dtype=np.float64), cents)
        if rng.random() < self.reverse_probability:
            grain = grain[::-1]
        grain = grain[: min(length, len(grain))]
        gain = scale * 10 ** (rng.uniform(-self.gain_jitter_db, self.gain_jitter_db) / 20)
        return grain * gain

    def generate(self, frames: int) -> np.ndarray:
        # Keep a crossfade's worth of tail unemitted so the next grain can blend into it.
        while len(self.buffer) < frames + self.crossfade:
            grain = self._grain()
            if not len(self.buffer):
                self.buffer = grain
                continue
            x = min(self.crossfade, len(grain), len(self.buffer))
            head = self.buffer[:-x] if x else self.buffer
            blend = self.buffer[-x:] * self.fade_out[-x:] + grain[:x] * self.fade_in[:x]
            self.junctions.append(self.buffer_start + len(head) + x // 2)
            self.buffer = np.concatenate([head, blend, grain[x:]])
        out = self.buffer[:frames]
        self.buffer = self.buffer[frames:]
        self.buffer_start += frames
        return out


class SampleEvent(EventGenerator):
    """One-shot from a licensed recording (bird call, frog, distant thunder), lightly re-pitched."""

    name = "sample"

    def setup(self, sources: list[str], pitch_cents: float = 80.0) -> None:
        if not sources:
            raise ValueError("SampleEvent needs at least one source")
        self.path = str(sources[int(self.rng.integers(len(sources)))])
        self.cents = float(self.rng.uniform(-pitch_cents, pitch_cents))

    def generate(self) -> np.ndarray:
        audio = np.asarray(_load(self.path, self.sample_rate), dtype=np.float64)
        audio = _repitch(audio, self.cents)
        edge = min(int(0.02 * self.sample_rate), len(audio) // 4)
        if edge:
            ramp = np.linspace(0, 1, edge)[:, None]
            audio[:edge] *= ramp
            audio[-edge:] *= ramp[::-1]
        return audio
