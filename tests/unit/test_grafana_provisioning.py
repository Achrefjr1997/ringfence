"""T-7.5 -- Grafana provisioning: datasource, dashboard provider, and the
RingFence dashboard JSON.

The dashboard is the source of truth (allowUiUpdates:false), so it is worth
asserting it stays wired to the provisioned datasource and only references
metrics the gateway actually exports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from packages.obs.metrics import MetricsSnapshot, prometheus_text

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "infra" / "monitoring" / "grafana"
DS = GRAFANA / "provisioning" / "datasources" / "prometheus.yml"
PROVIDER = GRAFANA / "provisioning" / "dashboards" / "ringfence.yml"
DASHBOARD = GRAFANA / "dashboards" / "ringfence-gateway.json"

_DS_UID = "ringfence-prometheus"


def _exported_metric_names() -> set[str]:
    """Every `ringfence_*` series name the gateway can emit."""
    text = prometheus_text(
        MetricsSnapshot(
            active=1,
            capacity=1,
            admitted=1,
            rejected={"AUTH": 1},
            active_by_tenant={"acme": 1},
            asr_breakers={"assemblyai": "closed"},
        )
    )
    return {
        line.split()[2]  # "# TYPE ringfence_<name> <kind>"
        for line in text.splitlines()
        if line.startswith("# TYPE ")
    }


def test_datasource_points_at_prometheus_and_is_default() -> None:
    doc = yaml.safe_load(DS.read_text(encoding="utf-8"))
    (src,) = doc["datasources"]
    assert src["type"] == "prometheus"
    assert src["url"] == "http://prometheus:9090"
    assert src["uid"] == _DS_UID
    assert src["isDefault"] is True


def test_dashboard_provider_loads_the_mounted_folder() -> None:
    doc = yaml.safe_load(PROVIDER.read_text(encoding="utf-8"))
    (prov,) = doc["providers"]
    assert prov["type"] == "file"
    assert prov["options"]["path"] == "/var/lib/grafana/dashboards"
    assert prov["allowUiUpdates"] is False


def test_dashboard_json_is_well_formed() -> None:
    dash = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert dash["uid"] == "ringfence-gateway"
    assert dash["title"]
    assert isinstance(dash["schemaVersion"], int)
    non_row = [p for p in dash["panels"] if p["type"] != "row"]
    assert len(non_row) >= 6  # overview stats + the time series


def test_every_panel_uses_the_provisioned_datasource() -> None:
    dash = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    for panel in dash["panels"]:
        if panel["type"] == "row":
            continue
        assert panel["datasource"]["uid"] == _DS_UID, panel["title"]
        for tgt in panel["targets"]:
            assert tgt["datasource"]["uid"] == _DS_UID, panel["title"]


def test_dashboard_only_references_metrics_the_gateway_exports() -> None:
    dash = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    exported = _exported_metric_names()
    referenced = set()
    for panel in dash["panels"]:
        if panel["type"] == "row":
            continue
        for tgt in panel["targets"]:
            referenced.update(re.findall(r"\bringfence_[a-z_]+\b", tgt["expr"]))
    assert referenced, "no ringfence_ metrics referenced at all — parsing bug"
    unknown = referenced - exported
    assert not unknown, f"dashboard references metrics the gateway never emits: {unknown}"
