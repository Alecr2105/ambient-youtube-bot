"""Resolves each recipe layer/event to real recordings.

Every item uses the local catalog and grows it from providers (own recordings, then
Freesound CC0) while it is below pool size. There is no synthetic fallback (owner's
decision, 2026-10-04): a required item with no usable recording fails the recipe, and the
daily selector moves on to another one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.audio import catalog
from app.audio.catalog import CatalogSound
from app.audio.generators.granular import _load
from app.audio.processor.loudness import loudness_profile
from app.audio.providers.base import AudioProvider, ProviderUnavailableError, SoundQuery, SoundUnusableError
from app.audio.providers.provenance import assess_metadata, speech_likelihood, unwanted_content
from app.audio.recipe import Event, Layer, LibrarySpec, Recipe
from app.licensing.licenses import LicenseRecord
from app.licensing.validator import LicenseValidator
from app.utils.config import LicenseType

log = logging.getLogger(__name__)

MAX_DOWNLOADS_PER_ITEM = 4
SPEECH_REJECT_THRESHOLD = 0.25
# A texture is a bed heard for hours: its loudest 3 s may not stand out from its usual level by
# more than this. Steady rain measured +3.8 to +4.2 LU; takes with thunder or gusts +8 to +15.
TEXTURE_MAX_SPREAD_LU = 6.0

ProviderFactory = Callable[[str, LibrarySpec], AudioProvider | None]


class SourceUnavailableError(RuntimeError):
    pass


@dataclass
class UsedResource:
    item: str
    kind: str
    provider: str
    asset_id: str | None
    license: LicenseRecord
    sound_id: int | None = None
    local_path: str | None = None


@dataclass
class Resolution:
    resources: list[UsedResource] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class SourceSelector:
    def __init__(self, session: Session, provider_factory: ProviderFactory, validator: LicenseValidator, cache_dir: Path, seed: int):
        self.session = session
        self.provider_factory = provider_factory
        self.validator = validator
        self.cache_dir = cache_dir
        self.rng = np.random.default_rng(np.random.SeedSequence([seed, 0x5E1EC7]))

    def resolve(self, recipe: Recipe, instance: dict[str, Any]) -> Resolution:
        result = Resolution()
        for spec, resolved in zip(recipe.layers, instance["layers"], strict=True):
            self._resolve_item(spec, resolved, "texture", result)
        for spec, resolved in zip(recipe.events, instance["events"], strict=True):
            self._resolve_item(spec, resolved, "event", result)
        return result

    def _resolve_item(self, spec: Layer | Event, resolved: dict[str, Any], kind: str, result: Resolution) -> None:
        sounds = self._library_sounds(spec.library, kind, result)
        if sounds:
            chosen = [sounds[i] for i in self.rng.permutation(len(sounds))[: spec.library.count]]
            paths = [s.local_path for s in chosen]
            if kind == "texture":
                resolved["generator"] = "granular"
                resolved["params"] = {**spec.granular.model_dump(), "sources": paths}
            else:
                resolved["generator"] = "sample"
                resolved["params"] = {"sources": paths, "pitch_cents": spec.pitch_cents}
            resolved["sound_ids"] = [s.id for s in chosen]
            for s in chosen:
                result.resources.append(UsedResource(spec.name, kind, s.provider, s.asset_id, s.license, s.id, s.local_path))
            return
        if spec.required:
            raise SourceUnavailableError(f"required {kind} '{spec.name}' has no usable source")
        result.notes.append(f"{spec.name}: skipped, no usable source")
        resolved["skipped"] = True

    def _library_sounds(self, library: LibrarySpec, kind: str, result: Resolution) -> list[CatalogSound]:
        available = catalog.find(self.session, library.category, kind, self.validator)
        if len(available) < library.pool_size:
            self._grow(library, kind, available, result)
            available = catalog.find(self.session, library.category, kind, self.validator)
        return available

    def _grow(self, library: LibrarySpec, kind: str, available: list[CatalogSound], result: Resolution) -> None:
        wanted = min(library.pool_size - len(available), MAX_DOWNLOADS_PER_ITEM)
        known = frozenset(s.asset_id for s in available)
        query = SoundQuery(library.query, kind, library.min_duration_s, library.max_duration_s, known)
        for provider_name in library.providers:
            if wanted <= 0:
                return
            provider = self.provider_factory(provider_name, library)
            if provider is None:
                continue
            try:
                candidates = provider.search(query)
            except ProviderUnavailableError as exc:
                result.notes.append(f"{provider_name}: {exc}")
                continue
            for candidate in candidates:
                if wanted <= 0:
                    break
                license_record = provider.license_info(candidate)
                if self.validator.problems(license_record):
                    continue
                notes = None
                if license_record.license_type is not LicenseType.OWN:
                    verdict = assess_metadata(candidate)
                    if not verdict.accepted:
                        log.debug("rejected %s/%s: %s", provider_name, candidate.asset_id, verdict.summary())
                        continue
                    if (noise := unwanted_content(candidate)):
                        log.info("rejected %s/%s: metadata mentions %s", provider_name, candidate.asset_id, ", ".join(noise))
                        continue
                    notes = verdict.summary()
                try:
                    fetched = provider.fetch(candidate, self.cache_dir)
                except ProviderUnavailableError as exc:
                    result.notes.append(f"{provider_name}: {exc}")
                    break
                except SoundUnusableError as exc:
                    log.info("rejected %s", exc)
                    continue
                features: dict[str, float] = {}
                if kind == "texture":
                    try:
                        audio = np.asarray(_load(str(fetched.local_path), 48000))
                    except Exception as exc:  # never let one bad file fail the day's video
                        log.info("rejected %s/%s: unreadable audio (%s)", provider_name, candidate.asset_id, exc)
                        continue
                    speech = speech_likelihood(audio, 48000)
                    if speech > SPEECH_REJECT_THRESHOLD:
                        log.info("rejected %s/%s: speech-like modulation %.2f", provider_name, candidate.asset_id, speech)
                        continue
                    _level, spread = loudness_profile(audio, 48000)
                    if spread > TEXTURE_MAX_SPREAD_LU:
                        # A "rain" take with a thunder clap, a gust or a door in it: fine to listen to once,
                        # wrong as a bed that is cut into grains and heard for hours.
                        log.info("rejected %s/%s: uneven texture, loudest 3 s +%.1f LU above its usual level",
                                 provider_name, candidate.asset_id, spread)
                        continue
                    features = {"speech_likelihood": speech, "loudness_spread_lu": round(spread, 2)}
                fetched.license = replace(license_record, provenance_notes=notes)
                catalog.register(self.session, fetched, library.category, kind, self.validator, features)
                wanted -= 1
                log.info("catalogued %s/%s for %s", provider_name, candidate.asset_id, library.category)
