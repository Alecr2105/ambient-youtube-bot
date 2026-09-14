from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    name: str
    passed: bool
    value: Any = None
    threshold: Any = None
    detail: str = ""


@dataclass
class QualityReport:
    section: str
    checks: list[CheckResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, value: Any = None, threshold: Any = None, detail: str = "") -> None:
        self.checks.append(CheckResult(name, bool(passed), _clean(value), _clean(threshold), detail))

    def failures(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "passed": self.passed, "checks": [asdict(c) for c in self.checks], "metrics": _clean(self.metrics)}

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3) if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):
        return _clean(value.item())
    return value
