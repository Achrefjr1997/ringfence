# Review notes

Decisions from code review that don't live cleanly in a commit message —
accepted trade-offs, and the reasoning, so they're findable later.

---

## R-B — `fx_family_emergency_ar_001` `transfer_request_t` = 25.0

**Status:** accepted (2026-09-07). Keep the fixture as-is.

**Background.** Commit `f5f10c6` (T-1.9) moved this fixture's
`transfer_request_t` from `22.0` to `25.0` in the same commit that stood
up the invariant suite. Review flagged the edit as **load-bearing**: the
invariant `test_all_fraud_fixtures_alert_by_the_transfer_request` *fails*
for this fixture at the original `22.0`, under both `<` and `<=`.

**Why the detector can't do better on this script.** The escalation is:

| t_end | CALLER turn | signals |
|---|---|---|
| 22.0 | "تحويل فوري: د17 ولا فلوسي … ما تقولش لأي حد" | RAIL_UNUSUAL, SECRECY |
| 25.0 | "الغرمة 1500 دينار، لازم تتحول قبل المساء" | URGENCY |
| 26.0 | *(CALLEE)* "حسنا، أنا بش نعمل التحويل توا." | — |

The state machine needs **two sustained turns ≥ alert** to reach ALERT.
The first qualifying turn is t=22; the second is t=25. So `first_alert_t`
is 25.0 at the earliest — it is structurally impossible to alert before
t=25 on this dialogue.

**The choice.**

- **(a)** revert to `22.0` and let the invariant fail for this one
  fixture, documented as "detector is one turn late on the
  family-emergency Derja script."
- **(b)** keep `25.0`. `transfer_request_t` marks the moment the
  *value transfer completes* — the money moves only when the victim
  complies at t=26, so a label at t=25 (the turn that finishes the ask)
  is defensible, and `first_alert_t (25) <= transfer_request_t (25)` means
  the alert lands *as* the ask completes, still ahead of the loss.

**Accepted (b).** The label reflects loss-timing, not detector
capability; the invariant stays green honestly. Revisit if the corpus
adds Derja fraud scripts where the ask completes in a single turn — those
would expose whether the detector is genuinely fast enough.

---

## R-A — `fr` VERIF_INVERT over-match

**Status:** fixed, commit `a98ddbc` (PR #4).

`"le code reçu par sms"` had no imperative verb, so it matched a bank
*refusing* to ask ("je ne vais surtout pas vous demander … le code reçu
par SMS") in `fx_real_bank_frauddesk_fr_001` (benign) — a spurious
`VERIF_INVERT` worth 28.9. Removed; `"lisez-moi le code"` /
`"lisez le code"` / `"donnez-moi le code"` cover the ask. frauddesk peak
28.9 → 0.0. The general problem (a bag-of-phrases matcher can't tell
asking from refusing) is unsolved — negation-aware matching in Tier 1, or
leaving it to the Tier-2 judge, would be the real fix.

---

## T-1.9 `<` → `<=`

The invariant `test_all_fraud_fixtures_alert_by_the_transfer_request` was
written in `f5f10c6` with `first_alert_t <= transfer_request_t` (an
earlier draft used `<`). A 2-of-2 hysteresis detector reaches ALERT on
the turn that completes the ask at the earliest, never strictly before
it, so `<=` is the correct bar: alerting *as* the ask lands is still
ahead of the money moving (which needs the victim to then comply). Noted
here because the change is not visible in `git` — the test file was born
in that commit, so there is no `<`-to-`<=` diff to review.
