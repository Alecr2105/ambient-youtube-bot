from __future__ import annotations

from collections.abc import Iterable

from app.licensing.licenses import LicenseRecord
from app.utils.config import LicenseType

EXTERNAL = {LicenseType.CC0, LicenseType.CC_BY_3, LicenseType.CC_BY_4, LicenseType.AI_COMMERCIAL}


class LicenseError(ValueError):
    pass


class LicenseValidator:
    def __init__(self, allowed: Iterable[LicenseType]):
        self.allowed = frozenset(allowed)

    def problems(self, record: LicenseRecord | None, blacklisted: bool = False, blacklist_reason: str | None = None) -> list[str]:
        if record is None:
            return ["no license record"]
        issues = []
        if record.license_type not in self.allowed:
            issues.append(f"license {record.license_type} not in ALLOWED_LICENSES")
        if blacklisted:
            issues.append(f"blacklisted: {blacklist_reason or 'no reason given'}")
        if record.license_type in EXTERNAL:
            if not record.source_url:
                issues.append("external resource without source_url")
            if not record.license_url:
                issues.append("external resource without license_url")
        if record.attribution_required and not record.author:
            issues.append("attribution required but author is missing")
        return issues

    def assert_usable(self, record: LicenseRecord | None, blacklisted: bool = False, blacklist_reason: str | None = None) -> None:
        issues = self.problems(record, blacklisted, blacklist_reason)
        if issues:
            raise LicenseError("; ".join(issues))
