import pytest

from packages.policy.tenants import (
    TenantConfig,
    TenantRegistry,
    event_subject,
    load_tenants,
    tenant_pattern,
)


def test_event_subject_and_pattern() -> None:
    assert event_subject("acme", "decision") == "rf.acme.decision"
    assert event_subject("acme", "session", "closed") == "rf.acme.session.closed"
    assert tenant_pattern("acme") == "rf.acme.*"
    with pytest.raises(ValueError):
        event_subject("", "decision")


def test_config_file_loads_the_example_tenants() -> None:
    reg = load_tenants()
    assert "acme" in reg.known() and "aircarrier" in reg.known()
    acme = reg.get("acme")
    assert acme.quota == 25 and acme.consent_required is True and acme.allow_cloud is True
    assert reg.get("aircarrier").allow_cloud is False


def test_unknown_tenant_gets_the_default_profile_with_its_own_id() -> None:
    reg = load_tenants()
    unknown = reg.get("brand-new-co")
    default = reg.get("default")
    assert unknown.tenant_id == "brand-new-co"
    assert unknown.quota == default.quota and unknown.allow_cloud == default.allow_cloud


def test_registry_without_a_config_still_has_a_default() -> None:
    reg = TenantRegistry({"default": TenantConfig(quota=7)})
    assert reg.get("whoever").quota == 7


def test_tenant_pattern_isolates_subjects() -> None:
    import fnmatch

    subs = ["rf.t1.decision", "rf.t2.decision", "rf.t1.session.closed", "rf.t2.turn"]
    assert [s for s in subs if fnmatch.fnmatch(s, tenant_pattern("t1"))] == [
        "rf.t1.decision",
        "rf.t1.session.closed",
    ]
