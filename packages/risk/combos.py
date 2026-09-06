from packages.contracts.risk import Contribution
from packages.policy.pack import PolicyPack
from packages.risk.scoring import EvidenceWindow


def evaluate_combos(window: EvidenceWindow, now: float, pack: PolicyPack) -> list[Contribution]:
    """Co-occurrence bonuses per production §6.1.

    Bonus values and window_s come from the policy pack (ComboSpec) —
    never hardcoded here. Signal structure (the AND/OR formula) lives in
    code; the two tunable numbers live in the pack. CALLER attribution
    falls out of EvidenceWindow.has()'s default role.
    """
    bonuses: list[Contribution] = []

    crit = pack.combos["COMBO_CRITICAL"]
    if window.has("AUTH_CLAIM", crit.window_s, now) and (
        window.has("VERIF_INVERT", crit.window_s, now)
        or window.has("RAIL_UNUSUAL", crit.window_s, now)
        or window.has("REMOTE_ACCESS", crit.window_s, now)
    ):
        bonuses.append(Contribution(source="combo", id="COMBO_CRITICAL", value=crit.bonus))

    iso = pack.combos["COMBO_ISOLATION"]
    if (
        window.has("SECRECY", iso.window_s, now)
        or window.has("CALLBACK_SUPPRESS", iso.window_s, now)
    ) and window.has("URGENCY", iso.window_s, now):
        bonuses.append(Contribution(source="combo", id="COMBO_ISOLATION", value=iso.bonus))

    classic = pack.combos["COMBO_CLASSIC"]
    if (
        window.has("AUTH_CLAIM", classic.window_s, now)
        and window.has("URGENCY", classic.window_s, now)
        and window.has("RAIL_UNUSUAL", classic.window_s, now)
    ):
        bonuses.append(Contribution(source="combo", id="COMBO_CLASSIC", value=classic.bonus))

    return bonuses
