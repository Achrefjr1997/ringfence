"""The live path must be able to speak French.

`apps/gateway/app.py` hard-coded ``language="en"`` on every capture session,
so the pipeline always loaded the English lexicon.  The 99-term French
lexicon and the French warning templates existed, were tested offline, and
could not run in production however a tenant was configured.  Nothing caught
it because every gateway test asserted on English fixtures.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.calls.ledger import InMemoryCallLedger
from packages.contracts.transcript import Turn
from packages.policy.tenants import TenantConfig, load_tenants, resolve_language
from packages.risk.lexicons import available_languages

SECRET = "test-session-secret"
SILENCE = b"\x00\x00" * 320


def _turn(text: str, order: int, t: float) -> Turn:
    return Turn(
        session_id="s",
        leg_id="far",
        turn_order=order,
        text=text,
        is_final=True,
        is_formatted=True,
        t_start=t,
        t_end=t + 2.0,
        words=(),
        confidence=1.0,
        language=None,
    )


# A French bank-impersonation script. Verified against fr.yaml: turns 2-4
# hit URGENCY, VERIF_INVERT and RAIL_UNUSUAL. None of it matches en.yaml.
_FR_SCRIPT = [
    _turn("Bonjour, je suis du service securite de votre banque.", 0, 1.0),
    _turn("C'est urgent, il faut agir immediatement.", 1, 4.0),
    _turn("Donnez-moi le code que vous avez recu par sms.", 2, 7.0),
    _turn("Il faut faire un virement vers un compte securise.", 3, 10.0),
]


# -- the resolver ------------------------------------------------------


def test_explicit_request_wins_when_the_tenant_allows_it() -> None:
    assert resolve_language("fr", TenantConfig(languages=["en", "fr"])) == "fr"


def test_a_caller_cannot_select_a_language_the_operator_did_not_enable() -> None:
    assert resolve_language("ar_tn", TenantConfig(languages=["en", "fr"])) == "en"


def test_tenant_default_is_its_first_configured_language() -> None:
    assert resolve_language(None, TenantConfig(languages=["ar_tn", "fr"])) == "ar_tn"


def test_a_language_we_ship_no_lexicon_for_is_never_returned() -> None:
    """load_lexicons would raise mid-call on one of these."""
    assert resolve_language("klingon", TenantConfig(languages=["klingon"])) == "en"
    assert resolve_language(None, TenantConfig(languages=[])) == "en"


def test_every_configured_tenant_resolves_to_a_shippable_lexicon() -> None:
    have = available_languages()
    for tid in ("default", "acme", "aircarrier"):
        assert resolve_language(None, load_tenants().get(tid)) in have, tid


# -- the live path -----------------------------------------------------


@pytest.fixture()
def ledger() -> InMemoryCallLedger:
    return InMemoryCallLedger()


def _client(ledger: InMemoryCallLedger) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR(_FR_SCRIPT, speed=400.0),
            session_secret=SECRET,
            call_ledger=ledger,
            dev_mode=True,
        )
    )


def _live_language(c: TestClient, session: str, lang: str) -> str:
    """Open a capture session and read back which lexicon it resolved to.

    Asserting on scores here would test the scripted-ASR harness rather than
    the resolution: turns arrive on the pipeline's own task and a socket that
    closes immediately never drains them.  /sessions reports the resolved
    language directly, which is both the thing under test and something an
    operator wants to see on a live call.
    """
    url = f"/ws/capture?session={session}&leg=far&tenant=acme&consent=ok&lang={lang}"
    with c.websocket_connect(url) as ws:
        ws.send_bytes(SILENCE)
        rows = c.get("/sessions?tenant=acme").json()
    return str(next(r["language"] for r in rows if r["session_id"] == session))


def test_a_french_call_runs_on_the_french_lexicon(ledger: InMemoryCallLedger) -> None:
    """The regression this file exists for: before the fix this was always
    "en", whatever the tenant or the request asked for."""
    with _client(ledger) as c:
        assert _live_language(c, "fr1", "fr") == "fr"


def test_english_is_still_selectable(ledger: InMemoryCallLedger) -> None:
    with _client(ledger) as c:
        assert _live_language(c, "en1", "en") == "en"


def test_an_unsupported_lang_request_falls_back_to_the_tenant_default(
    ledger: InMemoryCallLedger,
) -> None:
    """acme is configured [en, fr], so a nonsense request lands on en, not a
    crash mid-call when load_lexicons cannot find the file."""
    with _client(ledger) as c:
        assert _live_language(c, "zz1", "zz") == "en"


def test_the_french_script_would_score_on_fr_and_not_on_en() -> None:
    """Why the resolution matters, asserted where it can be asserted cleanly.

    The gateway picks the language; this is what that choice buys.
    """
    from packages.contracts.transcript import AttributedTurn
    from packages.policy.pack import load_pack
    from packages.risk.lexical import LexicalExtractor
    from packages.risk.lexicons import load_lexicons

    pack = load_pack("config/policy/default.yaml")
    weights = {sid: spec.weight for sid, spec in pack.signals.items()}

    def hits(lang: str) -> set[str]:
        ex = LexicalExtractor(load_lexicons(lang), weights)
        out: set[str] = set()
        for turn in _FR_SCRIPT:
            at = AttributedTurn(turn=turn, role="CALLER", role_confidence=1.0)
            out |= {h.signal_id for h in ex.extract(at)}
        return out

    assert hits("fr") >= {"URGENCY", "VERIF_INVERT", "RAIL_UNUSUAL"}
    assert hits("en") == set(), "French script must not match the English lexicon"
