"""``python -m apps.twilio`` — the Twilio ingress.

Environment:

    RF_TWILIO_AUTH_TOKEN   Twilio auth token; signs the webhook (required)
    RF_TWILIO_PUBLIC_URL   https://<tunnel-or-host> Twilio reaches us on
    RF_TWILIO_DIAL_TO      the protected person's phone, E.164
    RF_TWILIO_GATEWAY_KEY  RingFence API key for /ws/capture (required) --
                           ours, not Twilio's
    RF_GATEWAY_WS          capture URL (default ws://localhost:8000/ws/capture)
    RF_TWILIO_TENANT       tenant hint   RF_TWILIO_LANGUAGE  fr | en
    RF_TWILIO_CONSENT_TOKEN
    RF_TWILIO_PORT         listen port (default 8100)
"""

from __future__ import annotations

import logging
import os

import uvicorn

from apps.twilio.app import Config, create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.from_env()
    for name, value in (
        ("RF_TWILIO_AUTH_TOKEN", cfg.auth_token),
        ("RF_TWILIO_GATEWAY_KEY", cfg.api_key),
    ):
        if not value:
            raise SystemExit(f"{name} is required")
    if not cfg.public_url:
        raise SystemExit("RF_TWILIO_PUBLIC_URL is required (Twilio must reach this host)")
    port = int(os.environ.get("RF_TWILIO_PORT", "8100"))
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
