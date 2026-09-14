"""Mandatory gate before READY: every section must pass."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.quality.report import QualityReport


@dataclass
class GateResult:
    passed: bool
    sections: list[QualityReport]

    def failures(self) -> dict[str, list[str]]:
        return {s.section: [f"{c.name}: {c.value} (limit {c.threshold}) {c.detail}".strip() for c in s.failures()] for s in self.sections if not s.passed}

    def summary(self) -> str:
        if self.passed:
            return "all quality checks passed"
        return "; ".join(f"{section}: {', '.join(items)}" for section, items in self.failures().items())

    def write(self, path: Path) -> None:
        data = {
            "checked_at": datetime.now(UTC).isoformat(),
            "passed": self.passed,
            "failures": self.failures(),
            "sections": [s.to_dict() for s in self.sections],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


REQUIRED_SECTIONS = ("audio", "video", "metadata", "licenses", "duplicates")


def run_gate(sections: list[QualityReport]) -> GateResult:
    present = {s.section for s in sections}
    missing = [name for name in REQUIRED_SECTIONS if name not in present]
    if missing:
        placeholder = QualityReport("gate")
        placeholder.add("sections_present", False, missing, list(REQUIRED_SECTIONS))
        sections = [*sections, placeholder]
    return GateResult(all(s.passed for s in sections), sections)


def license_section(license_report) -> QualityReport:
    report = QualityReport("licenses")
    problems = {r["item"]: r["problems"] for r in license_report.data["resources"] if r["problems"]}
    report.add("all_resources_allowed", license_report.valid, problems, {})
    report.add("resources_recorded", bool(license_report.data["resources"]), len(license_report.data["resources"]), ">0")
    return report
