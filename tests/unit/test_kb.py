"""Fraud base-knowledge §2 — the static scam-pattern KB for the judge."""

from __future__ import annotations

from packages.risk.judge import DialogueWindow
from packages.risk.kb import Excerpt, StaticKnowledgeBase, load_excerpts

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
