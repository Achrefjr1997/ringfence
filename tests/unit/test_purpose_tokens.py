"""T-7.1d -- stateless single-purpose action tokens."""

from __future__ import annotations

import time

import pytest

from apps.gateway.purpose_tokens import (
    bind_for,
    issue_purpose_token,
    read_purpose_token,
)

SECRET = "unit-secret"


def test_round_trip_carries_purpose_subject_and_bind() -> None:
    tok = issue_purpose_token(purpose="reset", subject="u1", secret=SECRET, bind="abc123")
    claims = read_purpose_token(tok, purpose="reset", secret=SECRET)
    assert claims is not None
    assert claims.purpose == "reset" and claims.subject == "u1" and claims.bind == "abc123"


def test_a_token_only_reads_back_for_its_own_purpose() -> None:
    tok = issue_purpose_token(purpose="verify", subject="u1", secret=SECRET)
    assert read_purpose_token(tok, purpose="verify", secret=SECRET) is not None
    assert read_purpose_token(tok, purpose="reset", secret=SECRET) is None
    assert read_purpose_token(tok, purpose="invite", secret=SECRET) is None


def test_wrong_secret_or_tampered_signature_is_rejected() -> None:
    tok = issue_purpose_token(purpose="verify", subject="u1", secret=SECRET)
    assert read_purpose_token(tok, purpose="verify", secret="other") is None
    payload, _sig = tok.split(".", 1)
    assert read_purpose_token(f"{payload}.deadbeef", purpose="verify", secret=SECRET) is None
    assert read_purpose_token("not-a-token", purpose="verify", secret=SECRET) is None


def test_expiry_is_enforced() -> None:
    past = time.time() - 10
    tok = issue_purpose_token(purpose="reset", subject="u1", secret=SECRET, ttl_s=5, now=past)
    assert read_purpose_token(tok, purpose="reset", secret=SECRET) is None
    fresh = issue_purpose_token(purpose="reset", subject="u1", secret=SECRET, ttl_s=5)
    assert read_purpose_token(fresh, purpose="reset", secret=SECRET) is not None


def test_bind_for_changes_with_the_password_hash() -> None:
    # this is what makes a reset / invite token single-use
    assert bind_for("scrypt$a$b") != bind_for("scrypt$c$d")
    assert bind_for("scrypt$a$b") == bind_for("scrypt$a$b")
    assert len(bind_for("scrypt$a$b")) == 16


@pytest.mark.parametrize("purpose", ["verify", "reset", "invite"])
def test_default_ttls_are_all_in_the_future(purpose: str) -> None:
    tok = issue_purpose_token(purpose=purpose, subject="u1", secret=SECRET)  # type: ignore[arg-type]
    claims = read_purpose_token(tok, purpose=purpose, secret=SECRET)  # type: ignore[arg-type]
    assert claims is not None and claims.expires_at > time.time()
