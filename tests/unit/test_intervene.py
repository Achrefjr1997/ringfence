import uuid

import pytest

from packages.contracts.audio import Mode
from packages.contracts.risk import Contribution, Decision
from packages.intervene.service import InterventionService, Warning
from packages.policy.pack import load_pack

PACK = load_pack("config/policy/default.yaml")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (channel, text)

    async def deliver(self, warning: Warning, channel: str) -> None:
        self.calls.append((channel, warning.text))


def _decision(
    state: str, *, contribs: list[Contribution] | None = None, t: float = 20.0
) -> Decision:
    return Decision(
        decision_id=uuid.uuid4().hex,
        session_id="s1",
        t=t,
        state=state,  # type: ignore[arg-type]
        score=90.0,
        policy_pack="default@1",
        contributions=tuple(contribs or []),
    )


def _sig(sid: str, *, role: str = "CALLER", t: float = 18.0) -> Contribution:
    return Contribution(source="signal", id=sid, value=10.0, role=role, t=t)  # type: ignore[arg-type]


async def test_fires_only_on_intervene() -> None:
    svc = InterventionService(PACK, transport=RecordingTransport(), dry_run=False)
    assert await svc.on_decision(_decision("ALERT"), mode=Mode.SDK) is None
    assert await svc.on_decision(_decision("WATCH"), mode=Mode.SDK) is None
    w = await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=Mode.SDK)
    assert w is not None and w.delivered


async def test_picks_highest_weight_caller_signal_template() -> None:
    tr = RecordingTransport()
    svc = InterventionService(PACK, transport=tr, dry_run=False)
    d = _decision("INTERVENE", contribs=[_sig("URGENCY"), _sig("VERIF_INVERT"), _sig("AUTH_CLAIM")])
    w = await svc.on_decision(d, mode=Mode.SDK, language="en")
    assert w is not None and w.template_id == "VERIF_INVERT"  # weight 30, the highest
    assert all(txt == w.text for _, txt in tr.calls if _ != "guardian_push")


async def test_ignores_callee_signals_for_selection() -> None:
    svc = InterventionService(PACK, transport=RecordingTransport(), dry_run=False)
    d = _decision("INTERVENE", contribs=[_sig("VERIF_INVERT", role="CALLEE"), _sig("URGENCY")])
    w = await svc.on_decision(d, mode=Mode.SDK)
    assert w is not None and w.template_id == "URGENCY"


async def test_fans_out_to_all_channels_guardian_gets_no_text() -> None:
    tr = RecordingTransport()
    svc = InterventionService(PACK, transport=tr, dry_run=False)
    await svc.on_decision(_decision("INTERVENE", contribs=[_sig("RAIL_UNUSUAL")]), mode=Mode.SDK)
    channels = {c for c, _ in tr.calls}
    assert channels == set(PACK.interventions.channels)
    guardian_text = next(txt for c, txt in tr.calls if c == "guardian_push")
    assert guardian_text == ""  # verdict only, never content


async def test_cooldown_suppresses_repeat_within_window() -> None:
    clk = Clock()
    tr = RecordingTransport()
    svc = InterventionService(PACK, transport=tr, dry_run=False, now=clk)  # cooldown 60 s
    assert await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=Mode.SDK)
    clk.t = 30.0
    assert (
        await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=Mode.SDK)
        is None
    )
    clk.t = 70.0
    assert await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=Mode.SDK)
    # 2 fanouts * 3 channels
    assert len(tr.calls) == 2 * len(PACK.interventions.channels)


@pytest.mark.parametrize(
    "dry_run, mode",
    [(True, Mode.SDK), (True, Mode.CARRIER), (False, Mode.REPLAY)],
)
async def test_guard_suppresses_delivery(dry_run: bool, mode: Mode) -> None:
    tr = RecordingTransport()
    svc = InterventionService(PACK, transport=tr, dry_run=dry_run)
    w = await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=mode)
    assert w is not None  # the warning is still computed (for the console)
    assert w.delivered is False
    assert tr.calls == []  # but the transport is never touched


async def test_non_dry_non_replay_does_deliver() -> None:
    tr = RecordingTransport()
    svc = InterventionService(PACK, transport=tr, dry_run=False)
    w = await svc.on_decision(_decision("INTERVENE", contribs=[_sig("URGENCY")]), mode=Mode.SDK)
    assert w is not None and w.delivered
    assert tr.calls  # proves the suppression tests above are not vacuous
