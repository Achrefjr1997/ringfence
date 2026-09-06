import json
import uuid

import pytest

from packages.contracts.risk import Contribution, Decision
from packages.eval.campaign import (
    Fingerprint,
    correlate,
    fingerprint_case,
    main,
    similarity,
)
from packages.eval.run import run_report
from packages.intervene.cases import Case


def _fp(sid: str, sigs: set[str], lang: str = "en") -> Fingerprint:
    return Fingerprint(sid, "t", lang, frozenset(sigs), "INTERVENE")


def test_similarity_is_jaccard_and_language_gated() -> None:
    a = _fp("a", {"AUTH_CLAIM", "URGENCY", "VERIF_INVERT"})
    b = _fp("b", {"AUTH_CLAIM", "URGENCY", "RAIL_UNUSUAL"})
    assert similarity(a, b) == pytest.approx(2 / 4)
    assert similarity(a, _fp("c", a.signals, lang="fr")) == 0.0  # different language
    assert similarity(_fp("x", set()), _fp("y", set())) == 0.0


def test_correlate_groups_similar_calls_and_ignores_singletons() -> None:
    fps = [
        _fp("call1", {"AUTH_CLAIM", "URGENCY", "VERIF_INVERT", "CALLBACK_SUPPRESS"}),
        _fp("call2", {"AUTH_CLAIM", "URGENCY", "VERIF_INVERT"}),
        _fp("call3", {"AUTH_CLAIM", "URGENCY", "VERIF_INVERT", "SECRECY"}),
        _fp("lone", {"REMOTE_ACCESS", "SCRIPT_RIGIDITY"}),
        _fp("fr1", {"AUTH_CLAIM", "URGENCY", "VERIF_INVERT"}, lang="fr"),
    ]
    campaigns = correlate(fps, min_similarity=0.6)
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c.members == ["call1", "call2", "call3"]
    assert c.core_signals == frozenset({"AUTH_CLAIM", "URGENCY", "VERIF_INVERT"})
    assert c.languages == frozenset({"en"})  # the fr call is not similar to en calls


def test_fingerprint_from_a_case() -> None:
    d = Decision(
        decision_id=uuid.uuid4().hex,
        session_id="s9",
        t=20.0,
        state="INTERVENE",
        score=100.0,
        policy_pack="default@1",
        contributions=(
            Contribution(source="signal", id="AUTH_CLAIM", value=12.0, role="CALLER"),
            Contribution(source="signal", id="LISTENER_SIGNAL", value=0.0, role="CALLEE"),
            Contribution(source="combo", id="COMBO_CRITICAL", value=35.0),
        ),
    )
    case = Case(session_id="s9", opened_at=20.0, decisions=[d])
    fp = fingerprint_case(case, tenant_id="acme", language="fr")
    assert fp.signals == frozenset({"AUTH_CLAIM"})  # combo + CALLEE excluded
    assert fp.tenant_id == "acme" and fp.language == "fr"


def test_cli_over_a_report(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps(run_report()))
    assert main([str(rep)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fingerprints"] == 4  # the four fraud fixtures
    assert isinstance(out["campaigns"], list)
