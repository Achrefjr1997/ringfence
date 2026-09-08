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
