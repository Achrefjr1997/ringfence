"""T-7.4 -- rate limiting through the gateway."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from packages.contracts import settings as settings_mod

    monkeypatch.setenv("RF_RATELIMIT_AUTH_PER_MIN", "3")
    monkeypatch.setenv("RF_RATELIMIT_PER_MIN", "5")
    settings_mod.get_settings.cache_clear()
    app = create_app(provider_factory=lambda spec: NullASR([]), session_secret="s", dev_mode=True)
    try:
        yield TestClient(app)
    finally:
        settings_mod.get_settings.cache_clear()


def test_auth_surface_is_limited_with_a_retry_after(client: TestClient) -> None:
    body = {"email": "x@y.co", "password": "nope"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(6)]
    assert codes[:3] == [401, 401, 401]  # within budget: normal (bad creds)
    assert 429 in codes[3:]
    r = client.post("/auth/login", json=body)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_health_and_metrics_are_never_limited(client: TestClient) -> None:
    for _ in range(30):
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_the_default_lane_has_its_own_budget(client: TestClient) -> None:
    # exhaust the auth lane
    for _ in range(6):
        client.post("/auth/login", json={"email": "x@y.co", "password": "z"})
    # a non-auth path is still served
    assert client.get("/cases/none-such").status_code in (404, 200)
