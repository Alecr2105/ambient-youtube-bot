from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

TARGET_RATE = 48000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_normalized(path: Path, sample_rate: int = TARGET_RATE) -> np.ndarray:
    """Decode any libsndfile-readable file to float32 stereo at `sample_rate`."""
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    elif audio.shape[1] > 2:
        audio = audio[:, :2]
    if rate != sample_rate:
        ratio = Fraction(sample_rate, rate).limit_denominator(1000)
        audio = signal.resample_poly(audio, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)
    return audio


def normalize_to_flac(source: Path, destination: Path, sample_rate: int = TARGET_RATE) -> float:
    audio = load_normalized(source, sample_rate)
    peak = float(np.abs(audio).max())
    if peak > 0.999:
        audio = audio * (0.999 / peak)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".tmp.flac")
    sf.write(str(tmp), audio, sample_rate, subtype="PCM_24", format="FLAC")
    tmp.replace(destination)
    return len(audio) / sample_rate
