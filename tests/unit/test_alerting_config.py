"""T-7.5 -- the Prometheus / Alertmanager config ships and is coherent."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MON = ROOT / "infra" / "monitoring"
PROM = MON / "prometheus.yml"
RULES = MON / "alert-rules.yml"
AM = MON / "alertmanager.yml"
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.prod.yml"
METRICS_SRC = (ROOT / "packages" / "obs" / "metrics.py").read_text(encoding="utf-8")

# series the gateway actually exports (packages/obs/metrics.py) + Prometheus' own
_KNOWN_METRICS = {
    "ringfence_sessions_active",
    "ringfence_session_capacity",
    "ringfence_admitted_total",
    "ringfence_rejected_total",
    "ringfence_tenant_sessions_active",
    "ringfence_asr_breaker_state",
    "up",
}
_SEVERITIES = {"critical", "warning", "info"}


def _rules() -> list[dict]:
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [r for g in doc["groups"] for r in g["rules"]]


def test_ringfence_metric_names_are_still_the_ones_the_gateway_exports() -> None:
    for m in _KNOWN_METRICS - {"up"}:
        assert m.removeprefix("ringfence_") in METRICS_SRC, f"{m} no longer exported?"


def test_prometheus_scrapes_the_gateway_and_wires_alerting() -> None:
    doc = yaml.safe_load(PROM.read_text(encoding="utf-8"))
    jobs = {s["job_name"]: s for s in doc["scrape_configs"]}
    assert "ringfence-gateway" in jobs
    assert jobs["ringfence-gateway"]["static_configs"][0]["targets"] == ["gateway:8000"]
    assert any("alert-rules.yml" in f for f in doc["rule_files"])
    am = doc["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert am == ["alertmanager:9093"]


def test_every_alert_rule_is_well_formed_and_references_a_real_metric() -> None:
    rules = _rules()
    names = {r["alert"] for r in rules}
    assert {
        "GatewayDown",
        "ASRBreakerOpen",
        "HighRejectionRate",
        "AuthRejectionSpike",
        "NearSessionCapacity",
    } <= names

    for r in rules:
        assert r["for"], f"{r['alert']} has no `for:`"
        assert r["labels"]["severity"] in _SEVERITIES
        assert r["annotations"]["summary"] and r["annotations"]["description"]
        metrics_in_expr = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", r["expr"]))
        assert metrics_in_expr & _KNOWN_METRICS, f"{r['alert']} references no known metric"


def test_alertmanager_routes_to_a_webhook() -> None:
    doc = yaml.safe_load(AM.read_text(encoding="utf-8"))
    assert doc["route"]["receiver"] == "default-webhook"
    recv = {r["name"]: r for r in doc["receivers"]}
    hook = recv["default-webhook"]["webhook_configs"][0]
    assert hook["send_resolved"] is True
    assert "placeholder.invalid" in hook["url"]  # compose entrypoint substitutes it
    # critical alerts page more often than the default
    crit = next(x for x in doc["route"]["routes"] if 'severity = "critical"' in x["matchers"][0])
    assert crit["repeat_interval"] == "30m"


def test_prod_compose_runs_prometheus_and_alertmanager_internally() -> None:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    svc = doc["services"]
    assert {"prometheus", "alertmanager"} <= set(svc)
    for name in ("prometheus", "alertmanager"):
        assert "ports" not in svc[name], f"{name} must not publish a host port"
        assert svc[name]["restart"] == "unless-stopped"
    assert ":?" in svc["alertmanager"]["environment"]["RF_ALERT_WEBHOOK_URL"]
    mounts = " ".join(svc["prometheus"]["volumes"])
    assert "prometheus.yml" in mounts and "alert-rules.yml" in mounts
    assert {"prom_data", "am_data"} <= set(doc["volumes"])


def test_env_example_documents_the_alert_webhook() -> None:
    text = (ROOT / "infra" / "compose" / ".env.prod.example").read_text(encoding="utf-8")
    assert "RF_ALERT_WEBHOOK_URL=" in text


@pytest.mark.parametrize("path", [PROM, RULES, AM])
def test_yaml_files_parse(path: Path) -> None:
    assert yaml.safe_load(path.read_text(encoding="utf-8"))
