"""Budget: the thing standing between a detectable trigger and a real person's
attention. Tested with an injected clock, the GuardianWebhook pattern."""

from __future__ import annotations

from packages.verify.budget import VerificationBudget


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _budget(**kw: object) -> tuple[VerificationBudget, _Clock]:
    clock = _Clock()
    return VerificationBudget(now=clock, **kw), clock  # type: ignore[arg-type]


def test_a_fresh_session_is_allowed() -> None:
    b, _ = _budget()
    assert b.refuse_reason(session_id="s1", tenant="acme") is None


def test_a_session_verifies_at_most_once_ever() -> None:
    """A call re-entering INTERVENE five times must not contact the
    institution five times."""
    b, _ = _budget()
    b.claim(session_id="s1", tenant="acme")
    b.release()
    assert b.refuse_reason(session_id="s1", tenant="acme") == "already_verified"


def test_the_tenant_hourly_cap_holds_then_slides() -> None:
    b, clock = _budget(per_tenant_hourly=2)
    for i in range(2):
        assert b.refuse_reason(session_id=f"s{i}", tenant="acme") is None
        b.claim(session_id=f"s{i}", tenant="acme")
        b.release()
    assert b.refuse_reason(session_id="s9", tenant="acme") == "tenant_hourly"

    clock.t += 3601.0  # the window slides
    assert b.refuse_reason(session_id="s9", tenant="acme") is None


def test_one_tenants_cap_does_not_bind_another() -> None:
    b, _ = _budget(per_tenant_hourly=1)
    b.claim(session_id="s1", tenant="acme")
    b.release()
    assert b.refuse_reason(session_id="s2", tenant="acme") == "tenant_hourly"
    assert b.refuse_reason(session_id="s3", tenant="other") is None


def test_concurrency_is_capped_and_recovers_on_release() -> None:
    b, _ = _budget(max_concurrent=1)
    b.claim(session_id="s1", tenant="acme")
    assert b.refuse_reason(session_id="s2", tenant="acme") == "concurrent"
    b.release()
    assert b.refuse_reason(session_id="s2", tenant="acme") is None


def test_release_cannot_drive_the_live_count_negative() -> None:
    """An over-release would silently raise the real concurrency ceiling."""
    b, _ = _budget(max_concurrent=1)
    b.release()
    b.release()
    b.claim(session_id="s1", tenant="acme")
    assert b.refuse_reason(session_id="s2", tenant="acme") == "concurrent"


def test_the_seen_set_stays_bounded() -> None:
    """Invariant #6: a long-lived process must not accumulate session ids
    forever. Eviction is oldest-first."""
    b, _ = _budget(per_tenant_hourly=10_000, max_concurrent=10_000)
    for i in range(700):
        b.claim(session_id=f"s{i}", tenant="acme")
    assert len(b._seen) == 500
    assert b.refuse_reason(session_id="s699", tenant="acme") == "already_verified"
    assert b.refuse_reason(session_id="s0", tenant="acme") is None  # evicted
