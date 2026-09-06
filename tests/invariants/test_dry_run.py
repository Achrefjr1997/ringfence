"""Invariant #4 — no external side effect in dry-run or replay mode.

Asserted at the transport layer: ``httpx.AsyncClient.post`` is patched, every
fraud fixture is run through both egress paths (intervention service +
guardian webhook), and the mock must never be called.  A companion test
sends for real so this one is not vacuous.
"""

import httpx
import pytest

from packages.contracts.audio import Mode
from packages.eval.fixtures import iter_fixtures
from packages.eval.harness import run_fixture
from packages.intervene.http_transport import HttpTransport
from packages.intervene.service import InterventionService
from packages.intervene.webhook import GuardianWebhook
from packages.policy.pack import load_pack

PACK = load_pack("config/policy/default.yaml")


async def _run_interventions(*, dry_run: bool, mode: Mode) -> None:
    """Every fraud fixture's decisions through *both* egress paths — the
    intervention service and the guardian webhook."""
    svc = InterventionService(
        PACK, transport=HttpTransport("http://notify.invalid"), dry_run=dry_run
    )
    wh = GuardianWebhook(
        "http://guardian.invalid/hook", "k", PACK, dry_run=dry_run, max_per_hour=999
    )
    for fx in iter_fixtures(label="fraud"):
        for decision in run_fixture(fx.id).decisions:
            await svc.on_decision(decision, mode=mode, language=fx.language)
            await wh.notify(decision, mode=mode, rationale="test")


@pytest.mark.invariant
@pytest.mark.parametrize(
    "dry_run, mode",
    [(True, Mode.CARRIER), (True, Mode.SDK), (False, Mode.REPLAY)],
)
async def test_no_outbound_calls_in_dry_run_or_replay(
    monkeypatch: pytest.MonkeyPatch, dry_run: bool, mode: Mode
) -> None:
    calls: list[str] = []

    async def _spy(self: httpx.AsyncClient, url: str, **kw: object) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _spy)
    await _run_interventions(dry_run=dry_run, mode=mode)
    assert calls == [], f"{len(calls)} outbound notification(s) escaped the guard"


@pytest.mark.invariant
async def test_guard_is_not_vacuous_live_mode_does_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _spy(self: httpx.AsyncClient, url: str, **kw: object) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _spy)
    await _run_interventions(dry_run=False, mode=Mode.CARRIER)
    assert calls, "live mode sent nothing — the dry-run tests would be meaningless"
