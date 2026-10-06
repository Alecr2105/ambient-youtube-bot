from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RECIPES_DIR = Path(__file__).parent / "recipes"

Range = tuple[float, float]


def _as_range(value: Any) -> Any:
    if isinstance(value, int | float):
        return (float(value), float(value))
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Automation(Strict):
    depth_db: float = Field(1.5, ge=0, le=12)
    min_period_s: float = Field(30.0, gt=0)
    max_period_s: float = Field(180.0, gt=0)


class LayerEq(Strict):
    highpass_hz: float | None = None
    lowpass_hz: float | None = None
    low_shelf: tuple[float, float] | None = None  # (freq_hz, gain_db)
    high_shelf: tuple[float, float] | None = None


class LibrarySpec(Strict):
    """Where to find licensed recordings for a layer or event."""

    category: str = Field(pattern=r"^[a-z0-9_]+$")
    query: str
    min_duration_s: float = Field(gt=0)
    max_duration_s: float = Field(gt=0)
    count: int = Field(3, ge=1, le=50, description="recordings used per video")
    pool_size: int = Field(8, ge=1, le=200, description="catalog size to grow towards")
    providers: list[Literal["own", "freesound"]] = ["own", "freesound"]


class GranularSpec(Strict):
    grain_min_s: float = Field(6.0, gt=0)
    grain_max_s: float = Field(25.0, gt=0)
    crossfade_s: float = Field(4.0, ge=4.0)
    pitch_cents: float = Field(30.0, ge=0, le=100)
    gain_jitter_db: float = Field(1.5, ge=0, le=3)
    reverse_probability: float = Field(0.0, ge=0, le=1)


class Layer(Strict):
    """A continuous texture rebuilt for hours from real recordings (granular resynthesis).
    Every sound comes from the library: the owner's recordings or CC0 Freesound recordings."""

    name: str
    library: LibrarySpec
    granular: GranularSpec = GranularSpec()
    gain_db: Range = (0.0, 0.0)
    width: float = Field(1.0, ge=0, le=1.5)
    required: bool = True
    automation: Automation = Automation()
    intensity_depth_db: float = Field(0.0, ge=0, le=12)
    eq: LayerEq = LayerEq()

    coerce_ranges = field_validator("gain_db", mode="before")(_as_range)


class Event(Strict):
    """Short real recordings (a thunder clap, a bird call) dropped in at random times."""

    name: str
    library: LibrarySpec
    pitch_cents: float = Field(80.0, ge=0, le=300)
    required: bool = Field(False, description="true when the ambient makes no sense without this event")
    rate_per_hour: Range
    gain_db: Range
    min_gap_s: float = Field(20.0, ge=0)
    intensity_rate_scale: float = Field(0.0, ge=0, le=1)
    width: float = Field(1.0, ge=0, le=1.5)

    coerce_ranges = field_validator("rate_per_hour", "gain_db", mode="before")(_as_range)


class Intensity(Strict):
    """Program-wide slow arc (storm builds and eases) shared by all layers and events."""

    min_period_s: float = Field(600.0, gt=0)
    max_period_s: float = Field(2400.0, gt=0)


class Master(Strict):
    fade_in_s: float = Field(10.0, ge=0)
    fade_out_s: float = Field(20.0, ge=0)
    compressor_threshold_lu: float = Field(6.0, description="above integrated loudness")
    compressor_ratio: float = Field(1.5, ge=1)


class Music(Strict):
    """Lofi music generated for each video, played over the recipe's layers (then a quiet bed)."""

    styles: list[str] = Field(default_factory=list, description="keys of app.music.lofi.STYLES; empty = all")
    bed_below_lu: float = Field(15.0, ge=6, le=30, description="how far the ambience sits under the music")


class Recipe(Strict):
    slug: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    name_es: str | None = Field(None, description="only used for the optional 'es' localization, never in main fields")
    enabled: bool = True
    subniches: list[Literal["sleep", "study", "relaxation"]]
    keywords: list[str]
    visual_tags: list[str]
    costa_rica_eligible: bool = False
    layers: list[Layer] = Field(min_length=1)
    events: list[Event] = []
    intensity: Intensity = Intensity()
    master: Master = Master()
    music: Music | None = None

    @model_validator(mode="after")
    def _unique_names(self) -> Recipe:
        names = [item.name for item in [*self.layers, *self.events]]
        if len(names) != len(set(names)):
            raise ValueError("layer/event names must be unique")
        return self


def load_recipe(slug_or_path: str | Path) -> Recipe:
    path = Path(slug_or_path)
    if not path.suffix:
        path = RECIPES_DIR / f"{slug_or_path}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    return Recipe.model_validate(data)


def all_recipes() -> list[Recipe]:
    return [load_recipe(path) for path in sorted(RECIPES_DIR.glob("*.yaml"))]


def recipe_checksum(recipe: Recipe) -> str:
    return hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()


def pick(rng: np.random.Generator, value: Range) -> float:
    low, high = value
    return float(rng.uniform(low, high)) if high > low else float(low)


def instantiate(recipe: Recipe, seed: int) -> dict[str, Any]:
    """Resolve every range in the recipe into one concrete, reproducible variant."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0xA11B]))
    # "generator" and "params" are filled in by the source selector once the recordings are chosen.
    layers = [
        {"name": layer.name, "source": "library", "generator": None, "params": {}, "gain_db": pick(rng, layer.gain_db)}
        for layer in recipe.layers
    ]
    events = [
        {
            "name": event.name,
            "source": "library",
            "generator": None,
            "params": {},
            "rate_per_hour": pick(rng, event.rate_per_hour),
            "gain_db": list(event.gain_db),
        }
        for event in recipe.events
    ]
    return {"recipe": recipe.slug, "checksum": recipe_checksum(recipe), "seed": seed, "layers": layers, "events": events}
