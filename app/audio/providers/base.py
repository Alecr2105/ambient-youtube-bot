from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from app.licensing.licenses import LicenseRecord


@dataclass(frozen=True)
class SoundQuery:
    text: str
    kind: Literal["texture", "event"]
    min_duration_s: float
    max_duration_s: float
    exclude_ids: frozenset[str] = frozenset()


@dataclass
class SoundCandidate:
    provider: str
    asset_id: str
    name: str
    duration: float
    sample_rate: int
    channels: int
    file_type: str
    tags: list[str]
    description: str
    author: str | None
    source_url: str
    raw_license: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedSound:
    candidate: SoundCandidate
    local_path: Path  # normalized 48 kHz stereo FLAC in the cache
    checksum: str  # sha256 of the original download
    license: LicenseRecord


class ProviderUnavailableError(RuntimeError):
    """Provider not configured, out of quota or unreachable; the selector moves on."""


class SoundUnusableError(RuntimeError):
    """This one sound is unusable (short download, undecodable audio). The provider itself
    is healthy, so the selector skips this candidate and tries the next one."""


class AudioProvider(Protocol):
    name: str

    def search(self, query: SoundQuery) -> list[SoundCandidate]: ...

    def license_info(self, candidate: SoundCandidate) -> LicenseRecord | None: ...

    def fetch(self, candidate: SoundCandidate, cache_dir: Path) -> FetchedSound: ...

