from __future__ import annotations

from app.audio.generators.base import EventGenerator, Generator
from app.audio.generators.granular import GranularTexture, SampleEvent

# Every sound is a real recording (owner's decision, 2026-10-04): textures are rebuilt for hours
# by granular resynthesis, events are short recordings placed at random times. The source
# selector picks the recordings and wires them in.
LAYER_GENERATORS: dict[str, type[Generator]] = {GranularTexture.name: GranularTexture}
EVENT_GENERATORS: dict[str, type[EventGenerator]] = {SampleEvent.name: SampleEvent}
