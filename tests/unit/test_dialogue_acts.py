"""Dialogue acts: does composing beat enumerating?

The acceptance test for this extractor is not "does it fire" — the lexicon
fires too, on the thirteen phrasings someone wrote down.  It is whether it
holds up on the phrasings nobody wrote down.  ``test_paraphrases_*`` is that
test, and it is the reason the module exists.
"""

from __future__ import annotations

import pytest

from packages.contracts.transcript import AttributedTurn, Role, Turn
from packages.policy.pack import load_pack
from packages.risk.dialogue_acts import (
    ACT_DEMAND_RAIL,
    ACT_DEMAND_SECRET,
    ACT_REFUSAL,
    DialogueActExtractor,
)
from packages.risk.lexical import LexicalExtractor
from packages.risk.lexicons import load_lexicons

PACK = load_pack("config/policy/default.yaml")
WEIGHTS = {sid: spec.weight for sid, spec in PACK.signals.items()}


def _turn(text: str, role: Role = "CALLER") -> AttributedTurn:
    return AttributedTurn(
        turn=Turn(
            session_id="s",
            leg_id="far",
            turn_order=0,
            text=text,
            is_final=True,
            is_formatted=True,
            t_start=0.0,
            t_end=1.0,
            words=(),
            confidence=1.0,
        ),
        role=role,
        role_confidence=1.0,
    )


def _acts(text: str, role: Role = "CALLER", lang: str = "en") -> set[str]:
    ex = DialogueActExtractor(WEIGHTS, language=lang)
    return {h.signal_id for h in ex.extract(_turn(text, role))}


def _lexical(text: str, role: Role = "CALLER", lang: str = "en") -> set[str]:
    ex = LexicalExtractor(load_lexicons(lang), WEIGHTS)
    return {h.signal_id for h in ex.extract(_turn(text, role))}


# -- THE acceptance test ----------------------------------------------

# Every one of these is the same move as a phrase already in en.yaml, said
# a way nobody wrote down.  None is a trick: they are ordinary rewordings.
_PARAPHRASED_SECRET_DEMANDS = [
    "share the code with me",
    "what did the text message say",
    "give me those six digits",
    "just spell out the number on the back",
    "can you repeat the passcode for me",
    "send me your one-time code",
    "provide the verification code please",
    "what are the digits you received",
]

_PARAPHRASED_RAIL_DEMANDS = [
    "you'll need to send a wire today",
    "go ahead and purchase a voucher",
    "we can move it to a holding account",
    "what transfer limit does your bank allow",
]


@pytest.mark.parametrize("text", _PARAPHRASED_SECRET_DEMANDS)
def test_paraphrased_secret_demands_are_caught(text: str) -> None:
    assert ACT_DEMAND_SECRET in _acts(text), text


@pytest.mark.parametrize("text", _PARAPHRASED_RAIL_DEMANDS)
def test_paraphrased_rail_demands_are_caught(text: str) -> None:
    assert ACT_DEMAND_RAIL in _acts(text), text


def test_acts_beat_the_lexicon_on_rewritten_vocabulary() -> None:
    """The claim, measured. If this does not hold, the module is not earning
    its place and should be deleted rather than tuned."""
    corpus = _PARAPHRASED_SECRET_DEMANDS + _PARAPHRASED_RAIL_DEMANDS
    act_hits = sum(1 for t in corpus if _acts(t))
    lex_hits = sum(1 for t in corpus if _lexical(t))
    assert act_hits > lex_hits, f"acts {act_hits} vs lexical {lex_hits} of {len(corpus)}"
    assert act_hits >= len(corpus) - 1  # at most one miss tolerated


# -- invariant #1 stays untouchable ------------------------------------


@pytest.mark.parametrize("role", ["CALLEE", "UNKNOWN"])
def test_a_victim_reading_a_code_aloud_is_not_a_demand(role: Role) -> None:
    """A callee saying the words is a victim, not a threat."""
    assert _acts("the code is 4 8 2 9 1 1, should I read it to you", role) == set()
    assert _acts("give me the code", role) == set()


def test_refusal_is_scored_for_the_callee() -> None:
    assert ACT_REFUSAL in _acts("no, I won't give you that code", "CALLEE")
    assert ACT_REFUSAL in _acts("I'm not comfortable doing that", "CALLEE")


def test_refusal_weight_is_protective() -> None:
    assert PACK.signals[ACT_REFUSAL].weight < 0


# -- precision: ordinary speech must stay silent -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "your delivery is scheduled for tomorrow morning",
        "I wanted to confirm your appointment on Thursday",
        "the engineer will call you back before noon",
        "can you tell me what time suits you",
        "we will never ask you for your code",
        "how many people will be attending",
        "I'll send you an email with the details",
    ],
)
def test_benign_speech_produces_no_demand_act(text: str) -> None:
    assert ACT_DEMAND_SECRET not in _acts(text), text
    assert ACT_DEMAND_RAIL not in _acts(text), text


# -- French, since it is a target market -------------------------------


def test_french_secret_demand() -> None:
    assert ACT_DEMAND_SECRET in _acts("donnez-moi le code que vous avez reçu", lang="fr")


def test_french_rail_demand() -> None:
    assert ACT_DEMAND_RAIL in _acts("il faut faire un virement aujourd'hui", lang="fr")


def test_unknown_language_falls_back_to_english_not_a_crash() -> None:
    assert ACT_DEMAND_SECRET in _acts("read me the code", lang="ar_tn")


# -- plumbing ---------------------------------------------------------


def test_signals_absent_from_the_pack_are_skipped() -> None:
    ex = DialogueActExtractor({}, language="en")
    assert ex.extract(_turn("read me the code")) == []


def test_hit_carries_evidence_and_provenance() -> None:
    ex = DialogueActExtractor(WEIGHTS, language="en")
    (hit,) = [
        h for h in ex.extract(_turn("share the code with me")) if h.signal_id == ACT_DEMAND_SECRET
    ]
    assert hit.extractor == "dialogue_acts@1.0"
    assert "code" in hit.evidence.lower()
    assert hit.role == "CALLER"
