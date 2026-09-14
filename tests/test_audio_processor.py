from __future__ import annotations

import numpy as np
import pyloudnorm
import pytest

from app.audio.processor.dynamics import TruePeakLimiter, fade_gains
from app.audio.processor.filters import SosFilter, butter_sos
from app.audio.processor.loudness import LoudnessMeter, TruePeakMeter, true_peak_per_sample

SR = 48000


def noise(seconds: float, seed: int = 0, level: float = 0.1) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal((int(SR * seconds), 2)) * level


def test_loudness_matches_pyloudnorm():
    rng = np.random.default_rng(3)
    audio = noise(30) * np.linspace(0.2, 1.0, SR * 30)[:, None]
    audio[SR * 10 : SR * 12] = 0.0
    expected = pyloudnorm.Meter(SR).integrated_loudness(audio)
    meter = LoudnessMeter(SR)
    for start in range(0, len(audio), 7919):  # odd block size exercises carry-over
        meter.add(audio[start : start + 7919])
    assert meter.integrated() == pytest.approx(expected, abs=0.1)
    assert rng is not None


def test_silence_has_no_loudness():
    meter = LoudnessMeter(SR)
    meter.add(np.zeros((SR * 2, 2)))
    assert meter.integrated() == -np.inf


def test_true_peak_detects_intersample_peaks():
    t = np.arange(SR) / SR
    # fs/4 sine with 45° phase: samples sit at 0.707 of the true peak.
    tone = np.sin(2 * np.pi * SR / 4 * t + np.pi / 4)
    stereo = np.column_stack([tone, tone]) * 0.9
    assert np.abs(stereo).max() == pytest.approx(0.9 * np.sqrt(0.5), abs=1e-3)
    assert true_peak_per_sample(stereo).max() > 0.85


def test_streaming_true_peak_equals_whole_file():
    audio = noise(5, seed=1, level=0.3)
    whole = true_peak_per_sample(audio).max()
    meter = TruePeakMeter()
    for start in range(0, len(audio), 10007):
        meter.add(audio[start : start + 10007])
    assert meter.finish() == pytest.approx(whole, rel=1e-6)


def test_limiter_respects_ceiling_and_preserves_length():
    audio = noise(6, seed=2, level=0.05)
    audio[SR * 2 : SR * 2 + 200] += 0.95  # loud transient
    limiter = TruePeakLimiter(SR, ceiling_db=-1.3)
    out = [limiter(audio[s : s + 30011]) for s in range(0, len(audio), 30011)]
    out.append(limiter.flush())
    result = np.concatenate(out)
    assert result.shape == audio.shape
    assert TruePeakMeter.to_db(true_peak_per_sample(result).max()) <= -1.3 + 0.1
    quiet = slice(SR * 4, SR * 5)
    np.testing.assert_allclose(result[quiet], audio[quiet])


def test_sos_filter_is_block_size_invariant():
    audio = noise(2, seed=4)
    whole = SosFilter(butter_sos(SR, "bandpass", (300, 3000), 2))(audio)
    streamed_filter = SosFilter(butter_sos(SR, "bandpass", (300, 3000), 2))
    streamed = np.concatenate([streamed_filter(audio[s : s + 777]) for s in range(0, len(audio), 777)])
    np.testing.assert_allclose(streamed, whole, atol=1e-12)


def test_fades_start_and_end_at_zero():
    gains = fade_gains(0, 1000, 1000, 100, 200)
    assert gains[0] == 0.0 and gains[-1] == 0.0
    assert gains[500] == 1.0
    assert np.all(np.diff(gains[:100]) >= 0)
