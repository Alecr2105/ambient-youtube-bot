from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from app.audio.processor.loudness import LoudnessMeter
from app.quality.audio import AudioThresholds, check_audio, longest_run

SR = 48000


def write(path, audio):
    sf.write(path, audio, SR, subtype="PCM_24")
    return path


def at_loudness(audio: np.ndarray, target: float = -18.0) -> np.ndarray:
    meter = LoudnessMeter(SR)
    meter.add(audio)
    return audio * 10 ** ((target - meter.integrated()) / 20)


def pink(seconds: float, seed: int) -> np.ndarray:
    white = np.random.default_rng(seed).standard_normal((int(SR * seconds), 2))
    spectrum = np.fft.rfft(white, axis=0)
    freqs = np.fft.rfftfreq(len(white), 1 / SR)
    spectrum[1:] /= np.sqrt(freqs[1:, None])
    return np.fft.irfft(spectrum, n=len(white), axis=0)


def modulated(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed + 1000)
    envelope = np.repeat(rng.uniform(0.3, 1.0, int(seconds * 20)), SR // 20)
    return pink(seconds, seed) * envelope[: int(SR * seconds), None]


def thresholds(**kw):
    base = {"target_lufs": -18.0, "max_true_peak_dbtp": -1.0, "ignore_edges_s": 0.0}
    base.update(kw)
    return AudioThresholds(**base)


def test_clean_non_repeating_audio_passes(tmp_path):
    audio = at_loudness(modulated(120, seed=1))
    report = check_audio(write(tmp_path / "ok.flac", audio), thresholds(expected_duration_s=120))
    assert report.passed, [f"{c.name}={c.value}" for c in report.failures()]


def test_looped_audio_is_rejected(tmp_path):
    chunk = modulated(30, seed=2)
    looped = at_loudness(np.concatenate([chunk * g for g in (1.0, 0.9, 1.1, 1.0)]))
    report = check_audio(write(tmp_path / "loop.flac", looped), thresholds())
    loop = next(c for c in report.checks if c.name == "loop_detection")
    assert not loop.passed
    assert report.metrics["loop_lag_s"] == pytest.approx(30, abs=0.3)


def test_silence_gap_is_rejected(tmp_path):
    audio = modulated(60, seed=3)
    audio[SR * 20 : SR * 24] = 0
    report = check_audio(write(tmp_path / "gap.flac", at_loudness(audio)), thresholds())
    assert not next(c for c in report.checks if c.name == "silence").passed


def test_clipping_and_loudness_are_rejected(tmp_path):
    audio = modulated(40, seed=4) * 50
    report = check_audio(write(tmp_path / "hot.flac", np.clip(audio, -1, 1)), thresholds())
    failed = {c.name for c in report.failures()}
    assert {"clipping", "integrated_loudness", "true_peak"} <= failed


def test_wrong_duration_is_rejected(tmp_path):
    report = check_audio(write(tmp_path / "short.flac", at_loudness(modulated(30, seed=5))), thresholds(expected_duration_s=40))
    assert not next(c for c in report.checks if c.name == "duration").passed


def brown(seconds: float, seed: int) -> np.ndarray:
    white = np.random.default_rng(seed).standard_normal((int(SR * seconds), 2))
    spectrum = np.fft.rfft(white, axis=0)
    freqs = np.fft.rfftfreq(len(white), 1 / SR)
    spectrum[0] = 0
    spectrum[1:] /= np.maximum(freqs[1:, None], 20)
    return np.fft.irfft(spectrum, n=len(white), axis=0)


def test_junction_click_is_detected(tmp_path):
    audio = at_loudness(brown(20, seed=6))
    audio[SR * 10 :] += 0.2  # un-crossfaded splice in bass-heavy material: audible thump at 10 s
    path = write(tmp_path / "click.flac", audio)
    clicked = check_audio(path, thresholds(), junctions_s=[10.0])
    clean = check_audio(path, thresholds(), junctions_s=[5.0])
    assert not next(c for c in clicked.checks if c.name == "junction_clicks").passed
    assert next(c for c in clean.checks if c.name == "junction_clicks").passed


def calm_check(report):
    return next((c for c in report.checks if c.name == "calm_for_study"), None)


def test_study_ambient_rejects_a_startling_burst(tmp_path):
    """A thunder clap or a breaking wave is fine for sleep and wrong for studying."""
    audio = at_loudness(modulated(60, seed=7))
    startling = audio.copy()
    startling[SR * 30 : SR * 33] *= 10 ** (12 / 20)  # three loud seconds in the middle

    steady = check_audio(write(tmp_path / "steady.flac", audio), thresholds(max_short_term_jump_lu=5.0))
    burst = check_audio(write(tmp_path / "burst.flac", startling), thresholds(max_short_term_jump_lu=5.0))

    assert calm_check(steady).passed and calm_check(steady).value < 5.0
    assert not calm_check(burst).passed and calm_check(burst).value > 5.0


def test_calmness_is_only_checked_when_the_ambient_is_offered_for_studying(tmp_path):
    audio = at_loudness(modulated(60, seed=7))
    audio[SR * 30 : SR * 33] *= 10 ** (12 / 20)
    report = check_audio(write(tmp_path / "sleep.flac", audio), thresholds())
    assert calm_check(report) is None


def test_longest_run():
    assert longest_run(np.array([0, 1, 1, 0, 1, 1, 1, 0], dtype=bool)) == 3
    assert longest_run(np.zeros(5, dtype=bool)) == 0
