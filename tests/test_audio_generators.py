from __future__ import annotations

import numpy as np
import pytest

from app.audio.generators.base import NOMINAL_RMS, NoiseSource, SmoothRandom
from app.audio.generators.registry import EVENT_GENERATORS, LAYER_GENERATORS

SR = 48000


@pytest.mark.parametrize("name", sorted(LAYER_GENERATORS))
def test_layer_generator_output_is_sane(name):
    generator = LAYER_GENERATORS[name].create(SR, np.random.SeedSequence(7), duration_s=30)
    blocks = [generator.render(SR * 2) for _ in range(5)]
    audio = np.concatenate(blocks)
    assert audio.shape == (SR * 10, 2)
    assert np.isfinite(audio).all()
    rms = np.sqrt(np.mean(np.square(audio)))
    assert NOMINAL_RMS / 4 < rms < NOMINAL_RMS * 4
    # Block edges must not introduce discontinuities larger than the signal's own sample steps.
    steps = np.abs(np.diff(audio, axis=0)).max(axis=1)
    edges = [SR * 2 * i - 1 for i in range(1, 5)]
    assert steps[edges].max() <= np.percentile(steps, 99.99) * 1.5


@pytest.mark.parametrize("name", sorted(LAYER_GENERATORS))
def test_layer_generator_depends_on_seed(name):
    a = LAYER_GENERATORS[name].create(SR, np.random.SeedSequence(1), duration_s=10).render(SR * 3)
    b = LAYER_GENERATORS[name].create(SR, np.random.SeedSequence(2), duration_s=10).render(SR * 3)
    again = LAYER_GENERATORS[name].create(SR, np.random.SeedSequence(1), duration_s=10).render(SR * 3)
    np.testing.assert_allclose(a, again)
    assert abs(np.corrcoef(a[:, 0], b[:, 0])[0, 1]) < 0.5


@pytest.mark.parametrize("color", ["white", "pink", "brown"])
def test_noise_is_block_size_invariant(color):
    whole = NoiseSource(np.random.default_rng(5), SR, color)(SR)
    source = NoiseSource(np.random.default_rng(5), SR, color)
    streamed = np.concatenate([source(4800) for _ in range(10)])
    np.testing.assert_allclose(streamed, whole, atol=1e-9)


def test_noise_colors_have_expected_slope():
    def band_power(x, lo, hi):
        spectrum = np.abs(np.fft.rfft(x[:, 0])) ** 2
        freqs = np.fft.rfftfreq(len(x), 1 / SR)
        return spectrum[(freqs >= lo) & (freqs < hi)].mean()

    ratios = {}
    for color in ("white", "pink", "brown"):
        x = NoiseSource(np.random.default_rng(1), SR, color)(SR * 4)
        ratios[color] = 10 * np.log10(band_power(x, 200, 400) / band_power(x, 3200, 6400))
    assert abs(ratios["white"]) < 1.5
    assert 10 < ratios["pink"] < 14  # -3 dB/oct over 4 octaves
    assert 20 < ratios["brown"] < 28  # -6 dB/oct


def test_smooth_random_is_bounded_continuous_and_aperiodic():
    control = SmoothRandom(np.random.default_rng(0), 1000, 600, 2.0, 6.0)
    values = control.at(0, 600_000)
    assert values.min() >= -1 and values.max() <= 1
    assert np.abs(np.diff(values)).max() < 0.01
    np.testing.assert_allclose(control.at(1234, 500), values[1234:1734])
    intervals = np.diff(control.positions)
    assert intervals.std() > 0.5 * 1000


def test_thunder_event_is_normalized_and_decays():
    event = EVENT_GENERATORS["thunder"](SR, np.random.default_rng(3)).render()
    assert np.abs(event).max() == pytest.approx(1.0)
    assert len(event) > SR * 5
    assert np.abs(event[-100:]).max() < 0.05
