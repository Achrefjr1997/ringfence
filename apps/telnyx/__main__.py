"""``python -m apps.telnyx`` -- the Telnyx ingress.

Environment:

    RF_TELNYX_PUBLIC_KEY   account Ed25519 public key, base64 (required)
    RF_TELNYX_PUBLIC_URL   https://<tunnel-or-host> Telnyx reaches us on
    RF_TELNYX_DIAL_TO      the protected person's phone, E.164
    RF_TELNYX_API_KEY      RingFence API key for /ws/capture (required)
    RF_GATEWAY_WS          capture URL
    RF_TELNYX_TENANT / RF_TELNYX_LANGUAGE / RF_TELNYX_CONSENT_TOKEN
    RF_TELNYX_PORT         listen port (default 8101)
"""

from __future__ import annotations

import logging
import os

import uvicorn

from apps.telnyx.app import Config, create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.from_env()
    for name, value in (
        ("RF_TELNYX_PUBLIC_KEY", cfg.public_key),
        ("RF_TELNYX_API_KEY", cfg.api_key),
    ):
        if not value:
            raise SystemExit(f"{name} is required")
    if not cfg.public_url:
        raise SystemExit("RF_TELNYX_PUBLIC_URL is required (Telnyx must reach this host)")
    port = int(os.environ.get("RF_TELNYX_PORT", "8101"))
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
