"""Field recordings made by the channel owner, placed in assets/audio_own/<category>/.

Empty at the start; nothing depends on it. License is OWN.
"""

from __future__ import annotations

from pathlib import Path

import soundfile as sf

from app.audio.providers.base import FetchedSound, SoundCandidate, SoundQuery
from app.audio.providers.normalize import normalize_to_flac, sha256_file
from app.licensing.licenses import LicenseRecord
from app.utils.config import LicenseType

AUDIO_EXTENSIONS = {".wav", ".flac", ".aif", ".aiff"}


class OwnRecordingsProvider:
    name = "own"

    def __init__(self, root: Path, category: str):
        self.root = root
        self.category = category

    def search(self, query: SoundQuery) -> list[SoundCandidate]:
        folder = self.root / self.category
        if not folder.is_dir():
            return []
        candidates = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in AUDIO_EXTENSIONS or str(path) in query.exclude_ids:
                continue
            info = sf.info(str(path))
            if not query.min_duration_s <= info.duration <= query.max_duration_s:
                continue
            candidates.append(
                SoundCandidate(
                    provider=self.name, asset_id=str(path), name=path.stem, duration=info.duration,
                    sample_rate=info.samplerate, channels=info.channels, file_type=path.suffix.lstrip("."),
                    tags=[self.category], description="own field recording", author="channel owner",
                    source_url="", raw_license="OWN",
                )
            )
        return candidates

    def license_info(self, candidate: SoundCandidate) -> LicenseRecord:
        return LicenseRecord(LicenseType.OWN, None, "channel owner", title=candidate.name, verified_by="owner")

    def fetch(self, candidate: SoundCandidate, cache_dir: Path) -> FetchedSound:
        source = Path(candidate.asset_id)
        checksum = sha256_file(source)
        target = cache_dir / "sounds" / "own" / f"{checksum[:16]}.flac"
        if not target.exists():
            normalize_to_flac(source, target)
        return FetchedSound(candidate, target, checksum, self.license_info(candidate))

    def cost_estimate(self, query: SoundQuery) -> float:
        return 0.0


def manual_candidate(path: Path, license_type: LicenseType, source_url: str, author: str, title: str | None = None) -> SoundCandidate:
    """A sound the owner downloaded by hand (e.g. from Pixabay) and vouches for."""
    info = sf.info(str(path))
    return SoundCandidate(
        provider="manual", asset_id=sha256_file(path)[:32], name=title or path.stem, duration=info.duration,
        sample_rate=info.samplerate, channels=info.channels, file_type=path.suffix.lstrip("."), tags=[],
        description="manually imported", author=author, source_url=source_url, raw_license=license_type.value,
    )
