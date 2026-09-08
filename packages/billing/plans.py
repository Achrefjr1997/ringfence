"""Billing plans (T-7.3).

Plans are data in ``config/billing/plans.yaml`` -- loaded and validated the
same way as tenant config and policy packs.  An :class:`Org` carries a
``plan`` id (added in T-7.3); the :class:`PlanBook` resolves it, falling
back to the file's ``default``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "billing" / "plans.yaml"


class Plan(BaseModel):
    id: str
    name: str
    base_cents: int = Field(ge=0)
    included_lines: int = Field(ge=0)
    minutes_per_line: int = Field(ge=0)
    overage_cents_per_min: float = Field(ge=0)
    hard_cap: bool = False

    @property
    def included_minutes(self) -> int:
        """Pooled monthly allowance across all included lines."""
        return self.included_lines * self.minutes_per_line

    def overage_cents(self, minutes_used: float) -> int:
        over = max(0.0, minutes_used - self.included_minutes)
        return round(over * self.overage_cents_per_min)

    def month_cents(self, minutes_used: float) -> int:
        return self.base_cents + self.overage_cents(minutes_used)


class PlanBook:
    def __init__(self, plans: dict[str, Plan], default: str) -> None:
        if default not in plans:
            raise ValueError(f"default plan {default!r} is not defined")
        self._plans = plans
        self._default = default

    @property
    def default(self) -> Plan:
        return self._plans[self._default]

    def get(self, plan_id: str | None) -> Plan:
        if plan_id and plan_id in self._plans:
            return self._plans[plan_id]
        return self.default

    def ids(self) -> list[str]:
        return sorted(self._plans)


class _PlansFile(BaseModel):
    default: str
    plans: dict[str, dict[str, object]]

    @field_validator("plans")
    @classmethod
    def _not_empty(cls, v: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        if not v:
            raise ValueError("plans.yaml defines no plans")
        return v


def load_plans(path: Path | str | None = None) -> PlanBook:
    raw = yaml.safe_load(Path(path or _CONFIG).read_text(encoding="utf-8"))
    parsed = _PlansFile.model_validate(raw)
    plans = {pid: Plan.model_validate({"id": pid, **body}) for pid, body in parsed.plans.items()}
    return PlanBook(plans, parsed.default)


@lru_cache
def get_plans() -> PlanBook:
    return load_plans()
