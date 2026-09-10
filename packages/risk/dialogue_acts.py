"""Dialogue-act extraction (production §6.2, [P2]).

Look at what the lexicon has to say to catch one act:

    "read me the code"      "read the code"       "read me the numbers"
    "tell me the otp"       "give me your pin"    "what is the code"
    "what is your card number"                    "type in your password"

Nine phrasings of a single move — *ask the callee to hand over a secret* —
enumerated one string at a time.  That is why the lexicon does not
generalise: "share the code with me", "what did the text say", and "give me
those six digits" are the same act and all three miss.

This extractor composes the cross-product instead of enumerating it.  An act
is ``(act type) x (target)``:

* **Act type** comes from a closed, slow-changing class — a small set of
  transfer verbs, interrogative openers, and refusal frames.  These are
  function-word-shaped: an adversary rewriting a script changes the content
  words, not "give me" into something that is not a demand.
* **Target** comes from a closed set too — you cannot paraphrase "the
  six-digit code" into something that references neither a code nor digits,
  and ``NumericExtractor`` already covers the case where the digits are
  simply spoken.

The honest claim is not "no vocabulary".  It is that the *framing* vocabulary
is open and volatile while the *target* vocabulary is closed and stable, and
this keys on the stable half plus composition.  12 verbs x 13 targets covers
156 phrasings from 25 terms, before the interrogative and possessive
variants.

No new dependencies: this is regex over normalised text, reusing
``lexical._normalize`` so both extractors see identical input.
"""

from __future__ import annotations

import re

from packages.contracts.risk import SignalHit
from packages.contracts.transcript import AttributedTurn
from packages.risk.lexical import _normalize

ACT_DEMAND_SECRET = "ACT_DEMAND_SECRET"
ACT_DEMAND_RAIL = "ACT_DEMAND_RAIL"
ACT_REFUSAL = "ACT_REFUSAL"

# -- the closed classes ------------------------------------------------
# Transfer verbs: "hand this to me".  Deliberately small; a script writer
# varies the object and the pretext, not the act.
_VERBS = {
    "en": r"(?:read|tell|give|send|share|confirm|provide|type|enter|repeat|spell|say)",
    "fr": r"(?:lis|lisez|dis|dites|donne|donnez|envoie|envoyez|confirme|confirmez"
    r"|tape|tapez|saisis|saisissez|rep[eè]te|rep[eé]tez|communique|communiquez)",
}
# Rails take a different verb class: you are not asked to *say* a gift card,
# you are asked to buy one and move money onto it.
_RAIL_VERBS = {
    "en": r"(?:buy|purchase|load|move|transfer|send|wire|put|deposit|pay|get)",
    "fr": r"(?:ach[eè]te|achetez|acheter|charge|chargez|transf[eé]r\w*|envoie|envoyez"
    r"|vire|virez|d[eé]pose|d[eé]posez|payer|payez|faire)",
}
# "you'll need to…", "il faut…" -- necessity framing, no verb of demand.
_MUST = {
    "en": r"(?:you(?:'ll| will)? need to|you have to|you must|we need to|we can)",
    "fr": r"(?:il faut|il faudra|vous devez|vous devrez|on peut|nous pouvons)",
}
# Interrogative openers that request a value.
_ASK = {
    "en": r"(?:what(?:'s| is| are| was| does| did)?|which|how many)",
    "fr": r"(?:quel(?:le|s|les)?(?: est| sont)?|c'est quoi|combien)",
}
# Secrets: the thing that must never travel to a caller.
_SECRET = {
    "en": r"(?:code|pin|otp|password|passcode|cvv|card number|security number"
    r"|social security|one[- ]time|verification code|the numbers|digits|credentials"
    r"|number on the back)",
    "fr": r"(?:code|pin|mot de passe|cryptogramme|num[eé]ro de carte"
    r"|num[eé]ro de s[eé]curit[eé]|chiffres|identifiants)",
}
# Money rails a legitimate institution never routes a customer down.
_RAIL = {
    "en": r"(?:transfer|wire|gift card|prepaid|bitcoin|crypto|safe account"
    r"|holding account|western union|moneygram|voucher|transfer limit)",
    "fr": r"(?:virement|transf[eé]r|carte cadeau|pr[eé]pay[eé]|bitcoin|crypto"
    r"|compte s[eé]curis[eé]|western union|mandat)",
}
# Directedness: the demand is aimed at the listener.
_AT_YOU = {
    "en": r"(?:me|us|your|you)",
    "fr": r"(?:moi|nous|votre|vos|ton|ta|tes|vous)",
}
# Refusal frames — protective, and the callee's own words.
_REFUSAL = {
    "en": r"(?:i (?:won't|will not|am not going to|'m not going to|can't|cannot|refuse)"
    r"|i(?:'m| am) not (?:comfortable|going to|giving)|i don't (?:feel comfortable|think so))",
    "fr": r"(?:je (?:ne )?(?:vais pas|veux pas|peux pas|refuse)"
    r"|je (?:ne )?suis pas (?:[aà] l'aise|d'accord))",
}

_MAX_GAP = r"(?:\W+\w+){0,4}?\W+"  # up to four words between the parts


# "what does the text say" — asking the callee to relay a message they were
# sent is an OTP demand wearing a different hat.
_RELAY = {
    "en": r"what(?:'s| is| does| did)?(?:\W+\w+){0,3}?\W+(?:text|message|sms|email)"
    r"(?:\W+\w+){0,3}?\W+(?:say|says|said|arrive|arrived)",
    "fr": r"(?:qu(?:e|'est-ce qu)(?:e|i)?)(?:\W+\w+){0,3}?\W+(?:texto|sms|message)"
    r"(?:\W+\w+){0,3}?\W+(?:dit|disait)",
}


def _compile(lang: str) -> dict[str, re.Pattern[str]]:
    v, rv, ask, must = _VERBS[lang], _RAIL_VERBS[lang], _ASK[lang], _MUST[lang]
    at, secret, rail = _AT_YOU[lang], _SECRET[lang], _RAIL[lang]
    return {
        # "read me the code" / "give me those six digits" / "what is the code"
        # / "what did the text say"
        ACT_DEMAND_SECRET: re.compile(
            rf"\b(?:{v}\b{_MAX_GAP}?{secret}|{v}\s+{at}\b{_MAX_GAP}?{secret}"
            rf"|{ask}\b{_MAX_GAP}?{secret}|{_RELAY[lang]})",
            re.IGNORECASE,
        ),
        # "buy a voucher" / "move it to a holding account" / "you'll need to wire"
        ACT_DEMAND_RAIL: re.compile(
            rf"\b(?:{rv}\b{_MAX_GAP}?{rail}|{ask}\b{_MAX_GAP}?{rail}"
            rf"|{must}\b{_MAX_GAP}?{rail})",
            re.IGNORECASE,
        ),
        ACT_REFUSAL: re.compile(rf"\b{_REFUSAL[lang]}", re.IGNORECASE),
    }


class DialogueActExtractor:
    """Compose ``verb x target`` rather than enumerate the cross-product.

    Pure function of the turn, like every other extractor: no I/O, no model,
    no state.  ``ACT_REFUSAL`` is scored on any role — a callee refusing is
    the protective signal it exists for — while the two demand acts are
    CALLER-only, because a callee reading a code aloud is a victim, not a
    threat (invariant #1).
    """

    version = "dialogue_acts@1.0"

    def __init__(self, weights: dict[str, float], language: str = "en") -> None:
        self.weights = weights
        self._lang = language if language in _VERBS else "en"
        self._patterns = _compile(self._lang)

    def extract(self, turn: AttributedTurn) -> list[SignalHit]:
        norm, _idx = _normalize(turn.turn.text)
        if not norm:
            return []
        hits: list[SignalHit] = []
        for signal_id, pattern in self._patterns.items():
            weight = self.weights.get(signal_id)
            if weight is None:
                continue  # signal not in this pack
            if signal_id != ACT_REFUSAL and turn.role != "CALLER":
                continue
            m = pattern.search(norm)
            if m is None:
                continue
            hits.append(
                SignalHit(
                    signal_id=signal_id,
                    weight=float(weight),
                    role=turn.role,
                    t=turn.turn.t_end,
                    evidence=m.group(0)[:120],
                    evidence_span=(turn.turn.t_start, turn.turn.t_end),
                    extractor=self.version,
                )
            )
        return hits
