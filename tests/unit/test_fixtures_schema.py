from packages.eval.fixtures import iter_fixtures, load_fixture


def test_frauddesk_declares_derived_no_action_asked() -> None:
    """T-1.5/1.6 must derive NO_ACTION_ASKED for this call.

    The signal is call-level (absence of value-transfer signals at call
    end), not per-turn lexical — the fixture declares it structurally so
    the derived rule has a concrete target to satisfy.
    """
    fx = load_fixture("fx_real_bank_frauddesk_fr_001")
    assert fx.expect_call_level_signals == ["NO_ACTION_ASKED"]


def test_other_fixtures_default_to_no_call_level_signals() -> None:
    for fx in iter_fixtures():
        if fx.id == "fx_real_bank_frauddesk_fr_001":
            continue
        assert fx.expect_call_level_signals == [], fx.id