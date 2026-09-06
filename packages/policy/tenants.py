"""Tenant scoping (T-6.4).

A tenant is an isolation boundary: its own quota, provider policy, allowed
languages, and — the part that actually enforces isolation on the bus — a
subject prefix.  Every event is published under ``rf.<tenant>.…`` and a
subscriber for one tenant can never match another's subjects.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "tenants.yaml"


class TenantConfig(BaseModel):
    tenant_id: str = "default"
    quota: int = 100
    allow_cloud: bool = True
    consent_required: bool = False
    languages: list[str] = Field(default_factory=lambda: ["en", "fr", "ar_tn"])
    guardian_webhook_url: str | None = None


def event_subject(tenant_id: str, *parts: str) -> str:
    """Canonical bus subject: ``rf.<tenant>.<part>.<part>…``."""
    if not tenant_id:
        raise ValueError("tenant_id is required for an event subject")
    return ".".join(("rf", tenant_id, *parts))


def tenant_pattern(tenant_id: str) -> str:
    """Subscription pattern that matches only ``tenant_id``'s events."""
    return f"rf.{tenant_id}.*"


class TenantRegistry:
    def __init__(self, tenants: dict[str, TenantConfig]) -> None:
        self._tenants = tenants
        self._default = tenants.get("default", TenantConfig())

    def get(self, tenant_id: str) -> TenantConfig:
        cfg = self._tenants.get(tenant_id)
        if cfg is not None:
            return cfg
        # unknown tenant -> the default profile, but carrying its own id
        return self._default.model_copy(update={"tenant_id": tenant_id})

    def known(self) -> list[str]:
        return sorted(self._tenants)


def _load(path: Path) -> dict[str, TenantConfig]:
    if not path.exists():
        return {"default": TenantConfig()}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, TenantConfig] = {}
    for tid, body in (raw.get("tenants") or {}).items():
        out[tid] = TenantConfig.model_validate({"tenant_id": tid, **(body or {})})
    out.setdefault("default", TenantConfig())
    return out


@lru_cache
def load_tenants(path: str | Path = _CONFIG) -> TenantRegistry:
    return TenantRegistry(_load(Path(path)))
