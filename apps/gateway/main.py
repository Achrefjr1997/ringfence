"""ASGI entrypoint: ``uvicorn apps.gateway.main:app``."""

from apps.gateway.app import create_app

app = create_app()
