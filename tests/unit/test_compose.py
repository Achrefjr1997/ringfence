"""T-6.2 — the local compose file is valid and dry-run safe."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.yml"
DOCKERFILE = ROOT / "apps" / "gateway" / "Dockerfile"


def test_compose_parses_and_has_the_datastores_and_gateway() -> None:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = doc["services"]
    assert {"nats", "redis", "postgres", "minio", "gateway"} <= set(services)


def test_gateway_runs_dry_run() -> None:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = doc["services"]["gateway"]["environment"]
    assert env["RF_DRY_RUN"] == "true"  # the action plane sends nothing locally
    assert doc["services"]["gateway"]["ports"] == ["8000:8000"]


def test_dockerfile_targets_the_gateway_asgi_app() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "apps.gateway.main:app" in text
    assert "pip install" in text and "-e ." in text
