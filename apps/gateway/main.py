"""ASGI entrypoint: ``uvicorn apps.gateway.main:app``."""

from apps.gateway.app import create_app
from packages.contracts.settings import get_settings
from packages.obs.logging import configure_logging

_settings = get_settings()
configure_logging(level=_settings.log_level, fmt=_settings.log_format)

app = create_app()
