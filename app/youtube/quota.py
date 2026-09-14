from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import Engine, func, select

from app.database.models import QuotaUsage
from app.database.session import session_scope
from app.utils.config import Settings

COSTS_FILE = Path(__file__).with_name("quota_costs.yaml")
# YouTube quota resets at midnight Pacific Time.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")


class QuotaExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class MethodCost:
    bucket: str
    units: int


def load_costs(path: Path = COSTS_FILE) -> dict[str, MethodCost]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {method: MethodCost(entry["bucket"], int(entry["units"])) for method, entry in data.items()}


def quota_day(now: datetime | None = None) -> date:
    return (now or datetime.now(QUOTA_TIMEZONE)).astimezone(QUOTA_TIMEZONE).date()


class QuotaTracker:
    def __init__(self, engine: Engine, settings: Settings, costs: dict[str, MethodCost] | None = None):
        self.engine = engine
        self.costs = costs or load_costs()
        self.limits = {
            "general": settings.youtube_quota_general_daily,
            "uploads": settings.youtube_quota_uploads_daily,
            "search": settings.youtube_quota_search_daily,
        }

    def cost(self, method: str) -> MethodCost:
        if method not in self.costs:
            raise KeyError(f"no quota cost configured for {method}; add it to {COSTS_FILE.name}")
        return self.costs[method]

    def used(self, bucket: str, day: date | None = None) -> int:
        day = day or quota_day()
        with session_scope(self.engine) as session:
            total = session.scalar(
                select(func.coalesce(func.sum(QuotaUsage.units), 0)).where(QuotaUsage.bucket == bucket, QuotaUsage.quota_day == day)
            )
        return int(total)

    def remaining(self, bucket: str) -> int:
        return self.limits[bucket] - self.used(bucket)

    def ensure(self, method: str) -> None:
        cost = self.cost(method)
        if self.remaining(cost.bucket) < cost.units:
            raise QuotaExceededError(f"{method} needs {cost.units} units but the '{cost.bucket}' bucket is exhausted for {quota_day()}")

    def record(self, method: str, video_id: str | None = None) -> None:
        cost = self.cost(method)
        with session_scope(self.engine) as session:
            session.add(QuotaUsage(video_id=video_id, bucket=cost.bucket, method=method, units=cost.units, quota_day=quota_day()))
