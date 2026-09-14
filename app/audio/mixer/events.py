from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from app.audio.generators.base import SmoothRandom

if TYPE_CHECKING:
    from app.audio.recipe import Recipe

EDGE_MARGIN_S = 15.0


@dataclass(frozen=True)
class EventOccurrence:
    event: str
    onset: int
    gain_db: float
    seed: int

    def as_dict(self, sample_rate: int) -> dict[str, Any]:
        return {"event": self.event, "at_s": round(self.onset / sample_rate, 2), "gain_db": round(self.gain_db, 2)}


def schedule_events(
    recipe: Recipe,
    instance: dict[str, Any],
    duration_s: float,
    sample_rate: int,
    intensity: SmoothRandom,
    seed: np.random.SeedSequence,
) -> list[EventOccurrence]:
    """Inhomogeneous Poisson onsets (thinning), rate following the shared intensity arc,
    with a minimum gap. Never periodic: inter-onset times are exponential."""
    occurrences: list[EventOccurrence] = []
    seeds = seed.spawn(max(len(recipe.events), 1))
    for spec, resolved, event_seed in zip(recipe.events, instance["events"], seeds, strict=False):
        rng = np.random.default_rng(event_seed)
        base_rate = resolved["rate_per_hour"] / 3600
        if base_rate <= 0 or resolved.get("skipped"):
            continue
        max_rate = base_rate * (1 + spec.intensity_rate_scale)
        t, last = EDGE_MARGIN_S, -np.inf
        end = duration_s - EDGE_MARGIN_S
        while True:
            t += rng.exponential(1 / max_rate)
            if t >= end:
                break
            level = float(intensity.at_seconds(np.array([t]), sample_rate)[0])
            rate = base_rate * (1 + spec.intensity_rate_scale * level)
            if rng.random() > rate / max_rate or t - last < spec.min_gap_s:
                continue
            low, high = spec.gain_db
            gain = float(rng.uniform(low, high)) if high > low else float(low)
            occurrences.append(EventOccurrence(spec.name, int(t * sample_rate), gain, int(rng.integers(0, 2**63 - 1))))
            last = t
    return sorted(occurrences, key=lambda o: o.onset)
