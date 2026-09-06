"""Guardian webhook (T-4.5, §8.3).

Fires on ``INTERVENE``.  **Verdict only** — timestamp, signals, protective
signals, a <=25-word rationale.  **Never transcript, never audio.**
HMAC-SHA256 signed, rate-limited per guardian per hour, and it obeys the
dry-run / replay guard (invariant #4) like every other egress.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from collections.abc import Callable

import httpx

from packages.contracts.audio import Mode
from packages.contracts.risk import Decision
from packages.policy.pack import PolicyPack

_SIG_HEADER = "X-RingFence-Signature"
_TS_HEADER = "X-RingFence-Timestamp"
_HOUR = 3600.0


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(
    secret: str,
    body: bytes,
    signature: str,
    timestamp: str,
    *,
    now: float | None = None,
    tolerance_s: float = 300.0,
) -> bool:
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, body, timestamp), signature or "")


class GuardianWebhook:
    def __init__(
        self,
        url: str,
        secret: str,
        pack: PolicyPack,
        *,
        dry_run: bool = True,
        max_per_hour: int = 5,
        now: Callable[[], float] = time.time,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not secret:
            raise ValueError("guardian webhook requires a signing secret")
        self._url = url
        self._secret = secret
        self._protective = {sid for sid, spec in pack.signals.items() if spec.weight < 0}
        self._dry_run = dry_run
        self._max_per_hour = max_per_hour
        self._now = now
        self._client = client or httpx.AsyncClient(timeout=2.0)
        self._recent: deque[float] = deque()

    def _payload(self, decision: Decision, rationale: str) -> dict[str, object]:
        signals = sorted(
            {
                c.id
                for c in decision.contributions
                if c.source == "signal"
                and c.role in ("CALLER", None)
                and c.id not in self._protective
            }
        )
        protective = sorted({c.id for c in decision.contributions if c.id in self._protective})
        # verdict only — no transcript text, no audio, no evidence strings
        return {
            "event": "intervene",
            "session_id": decision.session_id,
            "decision_id": decision.decision_id,
            "t": decision.t,
            "state": decision.state,
            "score": round(decision.score, 1),
            "signals": signals,
            "protective": protective,
            "rationale": " ".join(rationale.split()[:25]),
        }

    async def notify(self, decision: Decision, *, mode: Mode, rationale: str = "") -> bool:
        if decision.state != "INTERVENE":
            return False

        now = self._now()
        while self._recent and now - self._recent[0] >= _HOUR:
            self._recent.popleft()
        if len(self._recent) >= self._max_per_hour:
            return False

        payload = self._payload(decision, rationale)
        if self._dry_run or mode is Mode.REPLAY:
            return False  # guard — never an external effect in dry-run / replay

        body = _canonical(payload)
        ts = f"{now:.0f}"
        await self._client.post(
            self._url,
            content=body,
            headers={
                "Content-Type": "application/json",
                _SIG_HEADER: sign(self._secret, body, ts),
                _TS_HEADER: ts,
            },
        )
        self._recent.append(now)
        return True
