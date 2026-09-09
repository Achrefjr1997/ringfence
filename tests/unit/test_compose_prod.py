"""T-7.2c -- the production compose stack, Caddyfile and release pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.prod.yml"
CADDYFILE = ROOT / "infra" / "compose" / "Caddyfile"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
REMOTE_SH = ROOT / "infra" / "deploy" / "remote.sh"
LOCAL_OVERLAY = ROOT / "infra" / "compose" / "docker-compose.local-prod.yml"


@pytest.fixture(scope="module")
def prod() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_stack_has_the_edge_proxy_and_the_datastores(prod: dict) -> None:
    assert {"caddy", "gateway", "nats", "redis", "postgres"} <= set(prod["services"])
    assert {"pg_data", "caddy_data", "caddy_config"} <= set(prod["volumes"])


def test_only_caddy_faces_the_internet(prod: dict) -> None:
    assert prod["services"]["caddy"]["ports"] == ["80:80", "443:443"]
    gw = prod["services"]["gateway"]
    assert "ports" not in gw  # no published port
    assert gw["expose"] == ["8000"]
    for svc in ("gateway", "nats", "redis", "postgres"):
        assert "ports" not in prod["services"][svc], f"{svc} must not publish a port"


def test_gateway_is_pulled_by_tag_never_built(prod: dict) -> None:
    gw = prod["services"]["gateway"]
    assert gw["image"].startswith("ghcr.io/") and "ringfence-gateway" in gw["image"]
    assert "${RF_IMAGE_TAG" in gw["image"]
    assert "build" not in gw


def test_gateway_runs_locked_down_with_required_secrets(prod: dict) -> None:
    env = prod["services"]["gateway"]["environment"]
    assert env["RF_DEV_MODE"] == "0"
    assert env["RF_LOG_FORMAT"] == "json"
    assert env["RF_MODE"] == "production"
    # the ":?" form makes compose abort if the env-file omits these
    assert "RF_SESSION_SECRET" in env and env["RF_SESSION_SECRET"].endswith("}")
    assert ":?" in env["RF_SESSION_SECRET"]
    assert ":?" in env["RF_DATABASE_URL"]


def test_everything_restarts_and_state_is_on_volumes(prod: dict) -> None:
    for svc in ("caddy", "gateway", "nats", "redis", "postgres"):
        assert prod["services"][svc]["restart"] == "unless-stopped"
    assert any("pg_data:" in v for v in prod["services"]["postgres"]["volumes"])


def test_grafana_is_internal_and_provisions_itself(prod: dict) -> None:
    g = prod["services"]["grafana"]
    assert g["image"] == "grafana/grafana:11.4.0"  # pinned
    assert "ports" not in g  # internal only, like prometheus / alertmanager
    assert g["restart"] == "unless-stopped"
    assert g["depends_on"] == ["prometheus"]
    # required admin password, same ${VAR:?} convention as the other secrets
    assert ":?" in g["environment"]["GF_SECURITY_ADMIN_PASSWORD"]
    assert g["environment"]["GF_USERS_ALLOW_SIGN_UP"] == "false"
    mounts = " ".join(g["volumes"])
    assert "/etc/grafana/provisioning:ro" in mounts
    assert "/var/lib/grafana/dashboards:ro" in mounts
    assert "grafana_data:/var/lib/grafana" in mounts
    assert "grafana_data" in prod["volumes"]


def test_caddy_config_binds_the_domain_and_proxies_the_gateway() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "{$RF_DOMAIN}" in text
    assert "reverse_proxy gateway:8000" in text
    assert "Strict-Transport-Security" in text
    assert "flush_interval -1" in text  # SSE not buffered


def test_release_pipeline_builds_then_gates_production() -> None:
    wf = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert {"build-push", "deploy-staging", "deploy-production"} <= set(jobs)
    assert jobs["deploy-production"]["environment"] == "production"  # manual approval
    assert jobs["deploy-production"]["needs"] == ["build-push", "deploy-staging"]
    # deploy jobs are inert until explicitly enabled
    assert "DEPLOY_STAGING_ENABLED" in jobs["deploy-staging"]["if"]
    assert "DEPLOY_PRODUCTION_ENABLED" in jobs["deploy-production"]["if"]
    assert wf["jobs"]["build-push"]["steps"][-1]["uses"].startswith("docker/build-push-action")


def test_env_prod_is_gitignored() -> None:
    assert ".env.prod" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_remote_deploy_script_shape() -> None:
    text = REMOTE_SH.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    # required inputs are asserted up front (the ${VAR:?} form)
    for var in ("SSH_HOST", "SSH_KEY", "AGE_KEY", "IMAGE_TAG"):
        assert var + ":?" in text
    # migrate runs before the stack comes up
    assert text.index("packages.db.migrate") < text.index("compose -f docker-compose.prod.yml")
    assert "sops --decrypt" in text and "shred -u age.key" in text


def test_gateway_waits_for_a_healthy_postgres(prod: dict) -> None:
    pg = prod["services"]["postgres"]
    assert "pg_isready" in pg["healthcheck"]["test"][1]
    assert prod["services"]["gateway"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_local_prod_overlay_builds_the_image_and_exposes_the_dashboards() -> None:
    doc = yaml.safe_load(LOCAL_OVERLAY.read_text(encoding="utf-8"))
    svc = doc["services"]
    assert "context" in svc["gateway"]["build"]  # built here, not pulled
    assert svc["gateway"]["ports"] == ["8000:8000"]  # direct access for debugging
    assert svc["prometheus"]["ports"] == ["9090:9090"]
    assert svc["alertmanager"]["ports"] == ["9093:9093"]
    assert svc["grafana"]["ports"] == ["3000:3000"]
    assert svc["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "true"
    assert svc["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_caddyfile_can_switch_to_an_internal_cert_for_localhost() -> None:
    assert "{$RF_TLS}" in CADDYFILE.read_text(encoding="utf-8")
