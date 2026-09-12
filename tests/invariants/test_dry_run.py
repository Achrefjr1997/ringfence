"""Invariant #4 — no external side effect in dry-run or replay mode.

Asserted at the transport layer, across **three** egress paths now:

* the intervention service and the guardian webhook, both of which POST --
  ``httpx.AsyncClient.post`` is patched, every fraud fixture is run through
  both, and the mock must never be called;
* the verification agent, which does **not** POST. It opens a WebSocket to
  AssemblyAI's Voice Agent API, which costs money per minute and starts a
  real conversation with a third party who never asked to be contacted. The
  post spy cannot see that, so the connect is spied instead.

A companion test for each guard exercises it in live mode, so neither is
vacuous -- a path that never egressed under any circumstance would otherwise
pass its own invariant.
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
from packages.verify.agent import VoiceAgentSession

PACK = load_pack("config/policy/default.yaml")


class _SpyConnect:
    """Stands in for the WebSocket dialer. Records, then refuses to proceed --
    any test that gets past this is asserting on the attempt, not the call."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    async def __call__(self, url: str, **kw: object) -> object:
        self.opened.append(url)
        raise _Reached


class _Reached(Exception):
    """The connect was reached. Carries no data; its occurrence is the fact."""


async def _run_verifications(*, dry_run: bool, mode: Mode) -> _SpyConnect:
    """Every fraud fixture's INTERVENE decisions, through the agent."""
    connect = _SpyConnect()
    session = VoiceAgentSession(api_key="k", connect=connect, dry_run=dry_run)
    for fx in iter_fixtures(label="fraud"):
        for decision in run_fixture(fx.id).decisions:
            if decision.state != "INTERVENE":
                continue
            try:
                await session.start(mode=mode)
            except (_Reached, NotImplementedError):
                pass  # live mode got through; that is what the spy records
    return connect


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


# -- the verification agent: a WebSocket, not a POST -------------------------


@pytest.mark.invariant
@pytest.mark.parametrize(
    "dry_run, mode",
    [(True, Mode.CARRIER), (True, Mode.SDK), (False, Mode.REPLAY)],
)
async def test_no_agent_session_in_dry_run_or_replay(dry_run: bool, mode: Mode) -> None:
    """Opening a Voice Agent session bills by the minute and speaks to a real
    third party. Neither a dry run nor a replay may ever start one."""
    connect = await _run_verifications(dry_run=dry_run, mode=mode)
    assert connect.opened == [], f"{len(connect.opened)} verification session(s) escaped the guard"


@pytest.mark.invariant
async def test_agent_guard_is_not_vacuous_live_mode_does_open() -> None:
    """Without this, an agent that never connected under any circumstance
    would satisfy the test above while proving nothing."""
    connect = await _run_verifications(dry_run=False, mode=Mode.CARRIER)
    assert connect.opened, "live mode opened nothing — the guard tests would be meaningless"
    assert all(u.startswith("wss://") for u in connect.opened)
