"""T-7.2b -- PgCaseStore pieces that need no live database."""

from __future__ import annotations

import json
import uuid

from packages.contracts.risk import Contribution, Decision
from packages.intervene.pg_cases import _contrib_rows, schema_sql


def test_schema_is_idempotent() -> None:
    sql = schema_sql()
    for table in ("cases", "case_decisions"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS ")
    assert sql.count("CREATE INDEX ") == sql.count("CREATE INDEX IF NOT EXISTS ")


def test_contrib_rows_drops_evidence_and_detail_text() -> None:
    # invariant #5: the two fields that can carry quoted call content must
    # never be written, whatever RF_RETAIN_TRANSCRIPTS is set to.
    d = Decision(
        decision_id=uuid.uuid4().hex,
        session_id="s1",
        t=20.0,
        state="ALERT",
        score=90.0,
        policy_pack="default@1",
        contributions=(
            Contribution(
                source="signal",
                id="URGENCY",
                value=10.0,
                role="CALLER",
                t=18.0,
                evidence="your account will be suspended today unless you act now",
            ),
            Contribution(
                source="judge",
                id="JUDGE_FRAUD",
                value=20.0,
                detail="the caller demands gift card codes to 'verify' the account",
            ),
        ),
    )
    rows = json.loads(_contrib_rows(d))
    assert [r["id"] for r in rows] == ["URGENCY", "JUDGE_FRAUD"]
    for r in rows:
        assert set(r) == {"source", "id", "value", "role", "t"}
    blob = _contrib_rows(d).lower()
    assert "suspended" not in blob and "gift card" not in blob
