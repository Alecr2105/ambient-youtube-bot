from __future__ import annotations

from collections.abc import Iterable

from app.licensing.licenses import LicenseRecord
from app.utils.config import LicenseType

LICENSE_LABELS = {LicenseType.CC_BY_3: "CC BY 3.0", LicenseType.CC_BY_4: "CC BY 4.0"}


def attribution_line(record: LicenseRecord) -> str:
    title = record.title or "Untitled"
    return f'"{title}" by {record.author} ({record.source_url}) licensed under {LICENSE_LABELS[record.license_type]}'


def attribution_block(records: Iterable[LicenseRecord]) -> str:
    """English credits for the video description; empty when nothing requires attribution."""
    seen: set[str | None] = set()
    lines = []
    for record in records:
        if record.attribution_required and record.source_url not in seen:
            seen.add(record.source_url)
            lines.append(attribution_line(record))
    if not lines:
        return ""
    return "Sound credits (from freesound.org):\n" + "\n".join(lines)
