from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import Engine, func, select

from app.database.models import ApiCost
from app.database.session import session_scope


class BudgetExceededError(RuntimeError):
    pass


class UsageLedger:
    """Records every external call in api_costs; answers request counts and spend."""

    def __init__(self, engine: Engine, daily_budget: float, monthly_budget: float):
        self.engine = engine
        self.daily_budget = daily_budget
        self.monthly_budget = monthly_budget

    def record(self, provider: str, operation: str, cost_usd: float = 0.0, units: float = 1.0, video_id: str | None = None, **detail) -> None:
        with session_scope(self.engine) as session:
            session.add(ApiCost(video_id=video_id, provider=provider, operation=operation, units=units, cost_usd=cost_usd, detail=detail))

    def _since(self, start: datetime, provider: str | None, column) -> float:
        query = select(func.coalesce(func.sum(column), 0.0)).where(ApiCost.created_at >= start)
        if provider:
            query = query.where(ApiCost.provider == provider)
        with session_scope(self.engine) as session:
            return float(session.scalar(query))

    def requests_today(self, provider: str) -> int:
        return int(self._since(_start_of_day(), provider, ApiCost.units))

    def requests_last_minute(self, provider: str) -> int:
        return int(self._since(datetime.now(UTC) - timedelta(minutes=1), provider, ApiCost.units))

    def spent_today(self) -> float:
        return self._since(_start_of_day(), None, ApiCost.cost_usd)

    def spent_this_month(self) -> float:
        now = datetime.now(UTC)
        return self._since(datetime(now.year, now.month, 1, tzinfo=UTC), None, ApiCost.cost_usd)

    def can_spend(self, amount: float) -> bool:
        if amount <= 0:
            return True
        return self.spent_today() + amount <= self.daily_budget and self.spent_this_month() + amount <= self.monthly_budget

    def require_budget(self, amount: float) -> None:
        if not self.can_spend(amount):
            raise BudgetExceededError(f"cost ${amount:.2f} exceeds remaining budget")


def _start_of_day() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
