"""T-7.2b -- PgCaseStore against a real Postgres.

Runs only with ``RF_TEST_DATABASE_URL`` set (see test_pg_identity.py).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.contracts.risk import Contribution, Decision  # noqa: E402
from packages.intervene.pg_cases import PgCaseStore  # noqa: E402

_TX = [("CALLER", "hi this is your bank", 1.0), ("CALLEE", "ok", 2.0)]


def _decision(state: str, *, t: float = 20.0, score: float = 90.0) -> Decision:
    return Decision(
        decision_id=uuid.uuid4().hex,
        session_id="s1",
        t=t,
        state=state,  # type: ignore[arg-type]
        score=score,
        policy_pack="default@1",
        contributions=(
            Contribution(source="signal", id="URGENCY", value=10.0, role="CALLER", t=18.0),
        ),
    )


@pytest.fixture()
def store() -> Iterator[PgCaseStore]:
    s = PgCaseStore(_DSN or "")
    s._run(_truncate(s))
    try:
        yield s
    finally:
        s.close()


async def _truncate(s: PgCaseStore) -> None:
    async with s._pool.acquire() as conn:
        await conn.execute("TRUNCATE case_decisions, cases RESTART IDENTITY CASCADE")


def test_record_opens_a_case_and_appends_decisions_in_order(store: PgCaseStore) -> None:
    store.record("s1", _decision("ALERT", t=20.0, score=80.0), _TX, tenant="org-1")
    case = store.record("s1", _decision("INTERVENE", t=25.0, score=100.0), _TX, tenant="org-1")

    assert case.session_id == "s1" and case.tenant == "org-1"
    assert case.opened_at == 20.0  # first decision's t
    assert [d.state for d in case.decisions] == ["ALERT", "INTERVENE"]
    assert case.peak_state == "INTERVENE" and case.peak_score == 100.0
    assert case.decisions[0].contributions[0].id == "URGENCY"


def test_transcript_is_withheld_unless_retention_is_on(
    store: PgCaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from packages.contracts import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("RF_RETAIN_TRANSCRIPTS", raising=False)
    case = store.record("s1", _decision("ALERT"), _TX)
    assert case.transcript == []  # invariant #5: nothing on disk

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("RF_RETAIN_TRANSCRIPTS", "true")
    case = store.record("s1", _decision("INTERVENE", t=25.0), _TX)
    assert [turn[0] for turn in case.transcript] == ["CALLER", "CALLEE"]
    settings_mod.get_settings.cache_clear()


def test_list_is_ordered_by_opened_at(store: PgCaseStore) -> None:
    store.record("late", _decision("ALERT", t=50.0), _TX)
    store.record("early", _decision("ALERT", t=10.0), _TX)
    assert [c.session_id for c in store.list()] == ["early", "late"]


def test_set_feedback_round_trips_and_validates(store: PgCaseStore) -> None:
    store.record("s1", _decision("ALERT"), _TX)

    case = store.set_feedback("s1", "fraud", "clear gift-card scam")
    assert case.feedback == "fraud" and case.feedback_note == "clear gift-card scam"
    assert store.get("s1").feedback == "fraud"  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="label must be one of"):
        store.set_feedback("s1", "bogus")
    with pytest.raises(KeyError):
        store.set_feedback("missing", "benign")


def test_a_second_store_sees_the_first_ones_writes(store: PgCaseStore) -> None:
    store.record("s1", _decision("ALERT"), _TX, tenant="org-1")
    other = PgCaseStore(_DSN or "")
    try:
        got = other.get("s1")
        assert got is not None and got.tenant == "org-1"
    finally:
        other.close()
