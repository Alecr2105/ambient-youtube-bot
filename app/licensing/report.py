from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.licensing.attribution import attribution_block
from app.licensing.validator import LicenseValidator

if TYPE_CHECKING:
    from app.audio.sources import UsedResource


@dataclass
class LicenseReport:
    data: dict[str, Any]

    @property
    def valid(self) -> bool:
        return bool(self.data["all_valid"])

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def build_license_report(run_id: str, resources: list[UsedResource], validator: LicenseValidator) -> LicenseReport:
    entries = []
    for resource in resources:
        record = resource.license
        problems = validator.problems(record)
        entries.append(
            {
                "item": resource.item,
                "kind": resource.kind,
                "provider": resource.provider,
                "asset_id": resource.asset_id,
                "sound_id": resource.sound_id,
                "title": record.title,
                "license_type": record.license_type.value,
                "license_url": record.license_url,
                "source_url": record.source_url,
                "author": record.author,
                "attribution_required": record.attribution_required,
                "provenance_notes": record.provenance_notes,
                "local_path": resource.local_path,
                "problems": problems,
            }
        )
    return LicenseReport(
        {
            "run_id": run_id,
            "allowed_licenses": sorted(license.value for license in validator.allowed),
            "all_valid": all(not entry["problems"] for entry in entries),
            "resources": entries,
            "attribution_block": attribution_block(r.license for r in resources),
        }
    )
