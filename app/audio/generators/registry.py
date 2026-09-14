from __future__ import annotations

from app.audio.generators.base import EventGenerator, Generator
from app.audio.generators.fire import Fireplace
from app.audio.generators.granular import GranularTexture, SampleEvent
from app.audio.generators.insects import Crickets
from app.audio.generators.noise import BrownNoise, PinkNoise, WhiteNoise
from app.audio.generators.rain import Rain, RainOnRoof, RainOnWindow
from app.audio.generators.thunder import Thunder
from app.audio.generators.water import OceanWaves, River, Waterfall
from app.audio.generators.wind import Wind

LAYER_GENERATORS: dict[str, type[Generator]] = {
    cls.name: cls
    for cls in (WhiteNoise, PinkNoise, BrownNoise, Rain, RainOnWindow, RainOnRoof, Wind, OceanWaves, River, Waterfall, Fireplace, Crickets)
}
# Sample-based generators need catalog files; the source selector wires them in.
SAMPLE_LAYER_GENERATORS: dict[str, type[Generator]] = {GranularTexture.name: GranularTexture}
ALL_LAYER_GENERATORS = {**LAYER_GENERATORS, **SAMPLE_LAYER_GENERATORS}

EVENT_GENERATORS: dict[str, type[EventGenerator]] = {cls.name: cls for cls in (Thunder, SampleEvent)}
