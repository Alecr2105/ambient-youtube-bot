from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.providers.base import FetchedSound
from app.database.models import License, Sound
from app.licensing.licenses import LicenseRecord
from app.licensing.validator import LicenseValidator


@dataclass(frozen=True)
class CatalogSound:
    id: int
    provider: str
    asset_id: str
    local_path: str
    duration: float
    license: LicenseRecord


def to_record(license_row: License, title: str | None = None) -> LicenseRecord:
    return LicenseRecord(
        license_type=license_row.license_type,
        source_url=license_row.source_url,
        author=license_row.author,
        license_url=license_row.license_url,
        restrictions=license_row.restrictions,
        provenance_notes=license_row.provenance_notes,
        verified_by=license_row.verified_by,
        title=title,
    )


def register(session: Session, fetched: FetchedSound, category: str, kind: str, validator: LicenseValidator, features: dict | None = None) -> Sound:
    """Add a fetched sound to the catalog. The license is validated before anything is stored."""
    validator.assert_usable(fetched.license)
    candidate = fetched.candidate
    existing = session.scalar(select(Sound).where(Sound.provider == candidate.provider, Sound.provider_asset_id == candidate.asset_id))
    if existing is not None:
        return existing
    record = fetched.license
    sound = Sound(
        name=candidate.name[:256],
        category=category,
        type=kind,
        source=candidate.source_url or str(fetched.local_path),
        provider=candidate.provider,
        provider_asset_id=candidate.asset_id,
        duration=candidate.duration,
        sample_rate=48000,
        channels=2,
        features={"tags": candidate.tags, **(features or {})},
        acquired_at=datetime.now(UTC),
        cost=0.0,
        local_path=str(fetched.local_path),
        checksum=fetched.checksum,
    )
    sound.license = License(
        license_type=record.license_type,
        license_url=record.license_url,
        source_url=record.source_url,
        author=record.author,
        attribution_required=record.attribution_required,
        attribution_text=None,
        restrictions=record.restrictions,
        provenance_notes=record.provenance_notes,
        verified_by=record.verified_by,
    )
    session.add(sound)
    session.flush()
    return sound


def find(session: Session, category: str, kind: str, validator: LicenseValidator) -> list[CatalogSound]:
    rows = session.scalars(select(Sound).where(Sound.category == category, Sound.type == kind).order_by(Sound.id)).all()
    usable = []
    for sound in rows:
        if sound.license is None:
            continue
        record = to_record(sound.license, sound.name)
        if validator.problems(record, sound.license.blacklisted, sound.license.blacklist_reason):
            continue
        usable.append(CatalogSound(sound.id, sound.provider, sound.provider_asset_id, sound.local_path, sound.duration, record))
    return usable


def blacklist(session: Session, sound_id: int, reason: str) -> None:
    sound = session.get(Sound, sound_id)
    if sound is None or sound.license is None:
        raise ValueError(f"sound {sound_id} not found")
    sound.license.blacklisted = True
    sound.license.blacklist_reason = reason
