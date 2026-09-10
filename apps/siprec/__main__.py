"""``python -m apps.siprec`` — run the SIPREC SRS -> ``/ws/capture`` bridge.

Configuration is environment only (it runs as a service):

    RF_SIPREC_BIND           host:port to listen on   (default 0.0.0.0:5060)
    RF_SIPREC_ADVERTISE_IP   IP put in the answer SDP (default 127.0.0.1)
    RF_GATEWAY_WS            capture URL               (default ws://localhost:8000/ws/capture)
    RF_SIPREC_API_KEY        gateway API key          (required)
    RF_SIPREC_CALLER_AOR     calling-party AOR        (optional; enables exact far/near)
    RF_SIPREC_TENANT         tenant hint              (optional; the key resolves it anyway)
"""

from __future__ import annotations

import asyncio
import logging

from apps.siprec.app import run


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
