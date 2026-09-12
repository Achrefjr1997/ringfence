"""The verification desk through the real gateway: tickets, audio, echo.

The desk logic is unit-tested in tests/unit/test_verify_desk.py; this checks
the Starlette adaptation -- above all that a bad ticket is refused before
``accept()``, so a guessed or replayed ticket never gets an open socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.gateway.app import _default_verifier, create_app
from apps.gateway.verify_desk import REJECT_CODE, UNAUTHORISED_CODE
from packages.asr.null import NullASR
from packages.contracts.settings import Settings
from packages.verify.desk import DeskExchange, DeskLine, Offer
from packages.verify.directory import Institution, get_directory
from packages.verify.live import VoiceAgentVerifier
from packages.verify.simulated import SimulatedVerifier

SECRET = "test-session-secret"


def _institution() -> Institution:
    inst = get_directory().get("amazon")
    assert inst is not None
    return inst


def _client(exchange: DeskExchange | None = None) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            dev_mode=True,
            desk_exchange=exchange,
        )
    )


async def _offer(exchange: DeskExchange) -> Offer:
    offer = exchange.offer(_institution())
    assert offer is not None
    return offer


async def _receive(line: DeskLine) -> bytes | None:
    return await asyncio.wait_for(line.receive_audio(), 2.0)


def test_an_unknown_ticket_is_refused_before_accept() -> None:
    with _client(DeskExchange()) as c:
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/ws/verify-desk/not-a-ticket"):
                pass
        assert refused.value.code == REJECT_CODE


def test_an_answered_ticket_bridges_audio_both_ways_and_cannot_be_reused() -> None:
    exchange = DeskExchange()
    with _client(exchange) as c:
        offer = c.portal.call(_offer, exchange)
        waiter = c.portal.start_task_soon(exchange.wait_answer, offer)
        with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}") as ws:
            line = waiter.result(timeout=2)
            assert line is not None

            c.portal.call(line.send_audio, b"\x01\x00\x02\x00")  # agent -> desk
            assert ws.receive_bytes() == b"\x01\x00\x02\x00"

            ws.send_bytes(b"\x03\x00\x04")  # desk -> agent; the odd byte is trimmed
            assert c.portal.call(_receive, line) == b"\x03\x00"

            c.portal.call(line.close, {"type": "ended", "verified": "false"})
            assert json.loads(ws.receive_text()) == {"type": "ended", "verified": "false"}

        with pytest.raises(WebSocketDisconnect) as reused:
            with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}"):
                pass
        assert reused.value.code == REJECT_CODE


def test_the_desk_hanging_up_reaches_the_agent() -> None:
    exchange = DeskExchange()
    with _client(exchange) as c:
        offer = c.portal.call(_offer, exchange)
        waiter = c.portal.start_task_soon(exchange.wait_answer, offer)
        with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}"):
            line = waiter.result(timeout=2)
            assert line is not None
        assert c.portal.call(_receive, line) is None


def test_oversized_frames_never_reach_the_agent() -> None:
    exchange = DeskExchange()
    with _client(exchange) as c:
        offer = c.portal.call(_offer, exchange)
        waiter = c.portal.start_task_soon(exchange.wait_answer, offer)
        with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}") as ws:
            line = waiter.result(timeout=2)
            assert line is not None
            ws.send_bytes(b"\x00" * 64_000)
            ws.send_bytes(b"\x07\x00")
            assert c.portal.call(_receive, line) == b"\x07\x00"


def test_the_echo_line_returns_what_it_is_sent() -> None:
    with _client(DeskExchange()) as c, c.websocket_connect("/ws/verify-desk/echo") as ws:
        ws.send_bytes(b"\x05\x00" * 1200)
        assert ws.receive_bytes() == b"\x05\x00" * 1200


def test_the_desk_page_is_served() -> None:
    with _client(DeskExchange()) as c:
        r = c.get("/verify-desk")
    assert r.status_code == 200 and "Verification desk" in r.text


def test_no_desk_routes_when_verification_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.gateway.app.get_settings", lambda: Settings(verify_enabled=False))
    with _client() as c:
        assert c.get("/verify-desk/offers").status_code == 404


# -- which verifier the gateway picks -----------------------------------------


def _settings(**kw: object) -> Settings:
    return Settings(verify_enabled=True, **kw)  # type: ignore[arg-type]


def test_simulated_unless_the_voice_agent_is_asked_for() -> None:
    v = _default_verifier(_settings(dry_run=False), DeskExchange(), api_key="k")
    assert isinstance(v, SimulatedVerifier)


def test_voice_agent_without_a_key_stays_simulated() -> None:
    cfg = _settings(verify_agent="voice_agent", dry_run=False)
    assert isinstance(_default_verifier(cfg, DeskExchange(), api_key=None), SimulatedVerifier)


def test_voice_agent_under_dry_run_stays_simulated() -> None:
    cfg = _settings(verify_agent="voice_agent", dry_run=True)
    assert isinstance(_default_verifier(cfg, DeskExchange(), api_key="k"), SimulatedVerifier)


def test_voice_agent_live_is_the_real_verifier() -> None:
    cfg = _settings(verify_agent="voice_agent", dry_run=False)
    assert isinstance(_default_verifier(cfg, DeskExchange(), api_key="k"), VoiceAgentVerifier)


# -- the desk token ----------------------------------------------------------


def _token_client(monkeypatch: pytest.MonkeyPatch, exchange: DeskExchange) -> TestClient:
    monkeypatch.setattr(
        "apps.gateway.app.get_settings",
        lambda: Settings(verify_enabled=True, verify_desk_token="desk-secret"),
    )
    return _client(exchange)


def test_offers_need_the_token_when_one_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    with _token_client(monkeypatch, DeskExchange()) as c:
        assert c.get("/verify-desk/offers").status_code == 401
        assert c.get("/verify-desk/offers?token=wrong").status_code == 401
        assert c.get("/verify-desk").status_code == 200  # the static page stays open


def test_a_wrong_token_is_refused_without_burning_the_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = DeskExchange()
    with _token_client(monkeypatch, exchange) as c:
        offer = c.portal.call(_offer, exchange)
        waiter = c.portal.start_task_soon(exchange.wait_answer, offer)
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}?token=wrong"):
                pass
        assert refused.value.code == UNAUTHORISED_CODE
        with c.websocket_connect(f"/ws/verify-desk/{offer.ticket}?token=desk-secret"):
            assert waiter.result(timeout=2) is not None


def test_the_echo_line_needs_the_token_too(monkeypatch: pytest.MonkeyPatch) -> None:
    with _token_client(monkeypatch, DeskExchange()) as c:
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/ws/verify-desk/echo"):
                pass
        assert refused.value.code == UNAUTHORISED_CODE
