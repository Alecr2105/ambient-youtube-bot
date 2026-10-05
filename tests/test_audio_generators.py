from __future__ import annotations

import numpy as np

from app.audio.generators.base import SmoothRandom
from app.audio.generators.registry import EVENT_GENERATORS, LAYER_GENERATORS


def test_only_recording_based_generators_are_registered():
    """Every sound is a real recording (owner's decision): nothing synthetic can be put in a recipe."""
    assert set(LAYER_GENERATORS) == {"granular"}
    assert set(EVENT_GENERATORS) == {"sample"}


def test_smooth_random_is_bounded_continuous_and_aperiodic():
    control = SmoothRandom(np.random.default_rng(0), 1000, 600, 2.0, 6.0)
    values = control.at(0, 600_000)
    assert values.min() >= -1 and values.max() <= 1
    assert np.abs(np.diff(values)).max() < 0.01
    np.testing.assert_allclose(control.at(1234, 500), values[1234:1734])
    intervals = np.diff(control.positions)
    assert intervals.std() > 0.5 * 1000
