from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils.config import LicenseType

CANONICAL_URLS = {
    LicenseType.CC0: "https://creativecommons.org/publicdomain/zero/1.0/",
    LicenseType.CC_BY_3: "https://creativecommons.org/licenses/by/3.0/",
    LicenseType.CC_BY_4: "https://creativecommons.org/licenses/by/4.0/",
}

REQUIRES_ATTRIBUTION = {LicenseType.CC_BY_3, LicenseType.CC_BY_4}


@dataclass(frozen=True)
class LicenseRecord:
    license_type: LicenseType
    source_url: str | None
    author: str | None
    license_url: str | None = None
    restrictions: str | None = None
    provenance_notes: str | None = None
    verified_by: str = "auto"
    title: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def attribution_required(self) -> bool:
        return self.license_type in REQUIRES_ATTRIBUTION


def normalize_license(raw: str | None) -> LicenseType | None:
    """Map a provider's license string or URL to a LicenseType. Anything unrecognised,
    non-commercial or retired (Sampling+) returns None and must not be used."""
    if not raw:
        return None
    text = raw.strip().lower()
    if re.search(r"\bnc\b|noncommercial|non-commercial|sampling", text):
        return None
    if "publicdomain/zero" in text or text in ("creative commons 0", "cc0", "cc0 1.0"):
        return LicenseType.CC0
    if "licenses/by/4.0" in text or text in ("attribution 4.0", "cc-by-4.0", "cc by 4.0"):
        return LicenseType.CC_BY_4
    if "licenses/by/3.0" in text or text in ("attribution 3.0", "cc-by-3.0", "cc by 3.0"):
        return LicenseType.CC_BY_3
    return None
