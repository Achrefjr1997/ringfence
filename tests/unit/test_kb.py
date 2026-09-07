"""Fraud base-knowledge §2 — the scam-pattern KB for the judge (static + BM25)."""

from __future__ import annotations

from packages.risk.judge import DialogueWindow
from packages.risk.kb import BM25KnowledgeBase, Excerpt, StaticKnowledgeBase, load_excerpts


def _win(
    caller: str, *, signals: tuple[str, ...] = (), protective: tuple[str, ...] = ()
) -> DialogueWindow:
    return DialogueWindow(
        session_id="s",
        turns=((("CALLER", caller, 9.0),)),
        score=40.0,
        active_signals=signals,
        active_protective=protective,
    )


_W1 = DialogueWindow(
    session_id="s1",
    turns=(("CALLER", "read me the code", 4.0),),
    score=40.0,
    active_signals=("VERIF_INVERT",),
    active_protective=(),
)
_W2 = DialogueWindow(
    session_id="s2",
    turns=(("CALLER", "your parcel is held", 2.0),),
    score=5.0,
    active_signals=(),
    active_protective=("OFFER_CALLBACK",),
)


def test_snapshot_loads_and_is_well_formed() -> None:
    ex = load_excerpts()
    assert len(ex) >= 15
    assert all(isinstance(e, Excerpt) for e in ex)
    assert all(e.label in ("fraud", "benign") for e in ex)
    assert all(e.text and e.scam_family and e.tactics for e in ex)
    assert len({e.id for e in ex}) == len(ex)  # ids unique
    labels = {e.label for e in ex}
    assert labels == {"fraud", "benign"}  # both represented


def test_examples_for_is_static_and_stable() -> None:
    kb = StaticKnowledgeBase.load()
    a = kb.examples_for(_W1)
    b = kb.examples_for(_W1)
    c = kb.examples_for(_W2)  # different window
    assert [e.id for e in a] == [e.id for e in b] == [e.id for e in c]
    assert len(a) == len(kb)


def test_head_of_the_list_is_label_balanced() -> None:
    kb = StaticKnowledgeBase.load()
    labels = [e.label for e in kb.examples_for(_W1)]
    n_benign = labels.count("benign")
    # the first 2*n_benign items strictly alternate fraud/benign; leftover
    # fraud (the KB has more fraud than benign) trails after.
    head = labels[: 2 * n_benign]
    assert head == ["fraud", "benign"] * n_benign
    assert {e.label for e in kb.examples_for(_W1)[:6]} == {"fraud", "benign"}


def test_k_caps_the_returned_set() -> None:
    kb = StaticKnowledgeBase.load(k=4)
    got = kb.examples_for(_W1)
    assert len(got) == 4
    assert {e.label for e in got} == {"fraud", "benign"}


def test_explicit_excerpts_preserve_within_label_order() -> None:
    xs = [
        Excerpt("f1", "a", "fraud", "x", ("t",)),
        Excerpt("b1", "b", "benign", "y", ("t",)),
        Excerpt("f2", "c", "fraud", "x", ("t",)),
        Excerpt("f3", "d", "fraud", "x", ("t",)),
    ]
    got = [e.id for e in StaticKnowledgeBase(xs).examples_for(_W1)]
    assert got == ["f1", "b1", "f2", "f3"]


# --- BM25 retrieval -------------------------------------------------------


def test_bm25_retrieves_the_on_topic_family_first() -> None:
    kb = BM25KnowledgeBase.load()
    bank = kb.examples_for(
        _win(
            "This is your bank fraud team. Do not hang up. Read me the one-time code we texted you.",
            signals=("AUTH_CLAIM", "CALLBACK_SUPPRESS", "VERIF_INVERT"),
        )
    )
    assert bank[0].scam_family == "bank_impersonation"

    tech = kb.examples_for(
        _win(
            "Microsoft security here. Your computer has a virus, install AnyDesk so I can fix it remotely.",
            signals=("REMOTE_ACCESS",),
        )
    )
    assert tech[0].scam_family == "tech_support"

    fam = kb.examples_for(
        _win(
            "Grandma it's me, I crashed the car and I'm in jail, please don't tell mom, send bail."
        )
    )
    assert fam[0].scam_family == "family_emergency"


def test_bm25_is_deterministic() -> None:
    kb = BM25KnowledgeBase.load()
    w = _win("install anydesk remote access to your computer", signals=("REMOTE_ACCESS",))
    assert [e.id for e in kb.examples_for(w)] == [e.id for e in kb.examples_for(w)]


def test_bm25_keeps_a_benign_contrast_floor() -> None:
    kb = BM25KnowledgeBase.load(k=6, min_benign=2)
    got = kb.examples_for(
        _win("read me the code, move the money to the safe account, buy gift cards now")
    )
    assert len(got) == 6
    assert sum(1 for e in got if e.label == "benign") >= 2


def test_bm25_empty_query_falls_back_to_static_order() -> None:
    kb = BM25KnowledgeBase.load(k=8)
    static = StaticKnowledgeBase.load(k=8)
    got = kb.examples_for(_win(""))  # no caller words, no signals
    assert [e.id for e in got] == [e.id for e in static.examples_for(_win(""))]


def test_bm25_scores_a_relevant_doc_above_an_irrelevant_one() -> None:
    xs = [
        Excerpt(
            "bank",
            "read me the security code from your bank text",
            "fraud",
            "bank",
            ("verification_inversion",),
        ),
        Excerpt(
            "weather",
            "it looks like rain this afternoon near the coast",
            "benign",
            "smalltalk",
            ("none",),
        ),
        Excerpt(
            "legit",
            "call the number on your card to verify this is really us",
            "benign",
            "bank_desk",
            ("offers_callback",),
        ),
    ]
    kb = BM25KnowledgeBase(xs, k=3, min_benign=1)
    got = [e.id for e in kb.examples_for(_win("give me the code from the bank text message"))]
    assert got[0] == "bank"
    assert got.index("bank") < got.index("weather")
