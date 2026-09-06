"""The real :class:`~packages.risk.judge.LLMCaller` — Ollama Cloud.

    export OLLAMA_API_KEY=...
    pip install -e ".[judge]"

``ollama`` is imported lazily so nothing else pays for it.  ``BoundedJudge``
owns the 800 ms deadline (``asyncio.wait_for``); this class just makes one
non-streaming, temperature-0, JSON-schema-constrained chat call.
"""

from __future__ import annotations

import os

_HOST = "https://ollama.com"
DEFAULT_MODEL = "gpt-oss:120b"


class OllamaCaller:
    def __init__(self, *, api_key: str | None = None, host: str = _HOST) -> None:
        key = api_key or os.environ.get("OLLAMA_API_KEY")
        if not key:
            raise RuntimeError("OllamaCaller needs OLLAMA_API_KEY (or api_key=)")
        try:
            from ollama import AsyncClient
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "OllamaCaller needs the 'judge' extra: pip install -e '.[judge]'"
            ) from exc
        self._client = AsyncClient(host=host, headers={"Authorization": f"Bearer {key}"})

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float,
        schema: dict[str, object],
    ) -> str:
        resp = await self._client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema,
            options={"temperature": temperature},
            stream=False,
        )
        return str(resp["message"]["content"] or "")
