"""ASGI entrypoint: ``uvicorn apps.gateway.main:app``.

``RF_DEV_MODE=1`` drops the API-key requirement on ``/ws/capture`` and
``/events`` and enables the ``?tenant=`` shortcut — for the local console
and manual testing only. Off by default.
"""

import os

from apps.gateway.app import create_app
from packages.contracts.settings import get_settings
from packages.obs.logging import configure_logging

_settings = get_settings()
configure_logging(level=_settings.log_level, fmt=_settings.log_format)

app = create_app(dev_mode=os.environ.get("RF_DEV_MODE", "").lower() in ("1", "true", "yes"))
