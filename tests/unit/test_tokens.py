"""T-7.1b — HMAC session tokens."""

from apps.gateway.tokens import DEFAULT_TTL_S, TokenClaims, issue_token, read_token


def test_round_trip() -> None:
    t = issue_token(user_id="u1", org_id="o1", role="admin", secret="s", now=1000.0)
    assert read_token(t, secret="s", now=1001.0) == TokenClaims(
        "u1", "o1", "admin", 1000.0 + DEFAULT_TTL_S
    )


def test_expires() -> None:
    t = issue_token(user_id="u", org_id="o", role="operator", secret="s", ttl_s=10.0, now=0.0)
    assert read_token(t, secret="s", now=9.999) is not None
    assert read_token(t, secret="s", now=10.0) is None


def test_wrong_secret_and_tampering_and_garbage() -> None:
    t = issue_token(user_id="u", org_id="o", role="admin", secret="s", now=0.0)
    assert read_token(t, secret="other", now=1.0) is None
    payload, sig = t.split(".", 1)
    assert read_token(f"{payload}x.{sig}", secret="s", now=1.0) is None
    assert read_token("no-dot", secret="s", now=1.0) is None
    assert read_token("", secret="s", now=1.0) is None
