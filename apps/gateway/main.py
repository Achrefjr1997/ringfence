"""ASGI entrypoint: ``uvicorn apps.gateway.main:app``.

``RF_DEV_MODE=1`` drops the API-key requirement on ``/ws/capture`` and
``/events`` and enables the ``?tenant=`` shortcut — for the local console
and manual testing only. Off by default.
"""

import os

from apps.gateway.app import create_app

app = create_app(dev_mode=os.environ.get("RF_DEV_MODE", "").lower() in ("1", "true", "yes"))
