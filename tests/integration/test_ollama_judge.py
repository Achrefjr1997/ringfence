"""Live Ollama Cloud judge check.  Runs only with the 'judge' extra + key:

    pip install -e ".[judge]"
    export OLLAMA_API_KEY=...
    pytest tests/integration/test_ollama_judge.py -q -m needs_ollama

Skips cleanly when either is missing.
"""

import importlib.util
import os
from pathlib import Path

import pytest

from packages.policy.pack import load_pack
from packages.risk.judge import BoundedJudge, DialogueWindow
from packages.risk.kb import StaticKnowledgeBase

pytestmark = pytest.mark.needs_ollama

REPO = Path(__file__).resolve().parents[2]
PACK = load_pack("config/policy/default.yaml")
CAP = PACK.judge.max_adjustment
MODEL = "gpt-oss:120b"


def _key() -> str | None:
    k = os.environ.get("OLLAMA_API_KEY")
    if k:
        return k
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OLLAMA_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def _judge(timeout_s: float = 20.0) -> BoundedJudge:
    if importlib.util.find_spec("ollama") is None:
        pytest.skip("ollama not installed")
    key = _key()
    if not key:
        pytest.skip("OLLAMA_API_KEY not set")
    from packages.risk.ollama_judge import OllamaCaller

    # KB attached — this is the production configuration (see _default_judge_factory).
    return BoundedJudge(
        OllamaCaller(api_key=key), model=MODEL, timeout_s=timeout_s, kb=StaticKnowledgeBase.load()
    )


SCAM = DialogueWindow(
    session_id="live-scam",
    turns=(
        ("CALLER", "This is your bank's fraud department. We detected a suspicious transfer.", 3.0),
        (
            "CALLER",
            "Do not hang up. Read me the one-time code we just texted you to cancel it.",
            9.0,
        ),
        ("CALLEE", "Okay, it's 4 8 2 9 1 5.", 12.0),
    ),
    score=62.0,
    active_signals=("AUTH_CLAIM", "CALLBACK_SUPPRESS", "VERIF_INVERT"),
    active_protective=(),
)

BENIGN = DialogueWindow(
    session_id="live-benign",
    turns=(
        ("CALLER", "Good morning, this is DHL. Your parcel arrives this afternoon.", 3.0),
        ("CALLER", "No payment is needed on delivery. Someone just needs to sign for it.", 8.0),
        ("CALLEE", "Great, I'll be home.", 10.0),
    ),
    score=4.0,
    active_signals=(),
    active_protective=("NO_ACTION_ASKED",),
)


async def test_scam_window_gets_a_positive_bounded_adjustment() -> None:
    v = await _judge().evaluate(SCAM, PACK)
    assert -CAP <= v.adjustment <= CAP
    assert v.model_version == MODEL
    assert v.latency_ms >= 0
    assert v.verdict in ("suspicious", "fraud")
    assert v.adjustment > 0


async def test_benign_window_is_not_pushed_toward_fraud() -> None:
    v = await _judge().evaluate(BENIGN, PACK)
    assert -CAP <= v.adjustment <= CAP
    assert v.verdict in ("benign", "unclear")
    assert v.adjustment <= 0


async def test_real_call_with_tiny_timeout_is_a_miss() -> None:
    v = await _judge(timeout_s=0.001).evaluate(SCAM, PACK)
    assert v.adjustment == 0
    assert "timeout" in v.rationale
