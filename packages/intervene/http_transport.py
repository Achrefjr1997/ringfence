"""An HTTP notification transport — the real egress the dry-run guard
protects.  ``InterventionService`` decides *whether* to call ``deliver``;
this class is the thing that actually reaches the network.
"""

from __future__ import annotations

import httpx

from packages.intervene.service import Warning


class HttpTransport:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=2.0)

    async def deliver(self, warning: Warning, channel: str) -> None:
        await self._client.post(
            f"{self._base_url}/notify/{channel}",
            json={
                "session_id": warning.session_id,
                "decision_id": warning.decision_id,
                "t": warning.t,
                "template_id": warning.template_id,
                "text": warning.text,
                "idempotency_key": warning.idempotency_key,
            },
            headers={"Idempotency-Key": warning.idempotency_key},
        )
