"""Oversight console P1 -- in-memory call ledger."""

from __future__ import annotations

from packages.calls.ledger import InMemoryCallLedger


def _ledger() -> InMemoryCallLedger:
    return InMemoryCallLedger()


def test_open_close_and_get_round_trip() -> None:
    lg = _ledger()
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=1000.0)
    lg.close("s1", ended_at=1090.0, leg_count=2)

    rec = lg.get("s1")
    assert rec is not None
    assert rec.tenant == "acme" and rec.api_key_id == "k1"
    assert rec.started_at == 1000.0 and rec.ended_at == 1090.0
    assert rec.duration_s == 90.0 and rec.leg_count == 2
    assert rec.peak_state == "CALM" and rec.peak_score == 0.0


def test_record_score_tracks_the_peak_and_the_series() -> None:
    lg = _ledger()
    lg.open("s1", tenant="acme", api_key_id=None, started_at=0.0)
    lg.record_score("s1", t=5.0, score=12.0, state="CALM")
    lg.record_score("s1", t=20.0, score=61.0, state="ALERT")
    lg.record_score("s1", t=40.0, score=48.0, state="WATCH")  # backs off

    rec = lg.get("s1")
    assert rec is not None
    assert rec.peak_state == "ALERT" and rec.peak_score == 61.0
    assert [p.state for p in rec.scores] == ["CALM", "ALERT", "WATCH"]
    assert [p.t for p in rec.scores] == [5.0, 20.0, 40.0]


def test_writes_to_an_unknown_session_are_ignored() -> None:
    lg = _ledger()
    lg.record_score("ghost", t=1.0, score=90.0, state="INTERVENE")
    lg.close("ghost")
    assert lg.get("ghost") is None


def test_list_filters_by_key_time_and_state() -> None:
    lg = _ledger()
    lg.open("a", tenant="acme", api_key_id="k1", started_at=100.0)
    lg.open("b", tenant="acme", api_key_id="k2", started_at=200.0)
    lg.open("c", tenant="acme", api_key_id="k1", started_at=300.0)
    lg.open("z", tenant="other", api_key_id="k1", started_at=300.0)
    lg.record_score("b", t=1.0, score=70.0, state="ALERT")

    # tenant scoping
    assert {r.session_id for r in lg.list("acme")} == {"a", "b", "c"}
    # newest first
    assert [r.session_id for r in lg.list("acme")] == ["c", "b", "a"]
    # by key
    assert {r.session_id for r in lg.list("acme", api_key_id="k1")} == {"a", "c"}
    # by time window
    assert {r.session_id for r in lg.list("acme", since=150.0, until=300.0)} == {"b"}
    # by min state
    assert [r.session_id for r in lg.list("acme", min_state="ALERT")] == ["b"]
    # limit
    assert len(lg.list("acme", limit=1)) == 1


def test_list_rows_carry_no_score_series_but_get_does() -> None:
    lg = _ledger()
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=0.0)
    lg.record_score("s1", t=1.0, score=10.0, state="CALM")
    assert lg.list("acme")[0].scores == ()
    assert len(lg.get("s1").scores) == 1  # type: ignore[union-attr]
