import pytest


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> None:
    """sse-starlette keeps a module-global ``should_exit_event`` bound to the
    first event loop that touches it; a second TestClient (new loop) then
    fails with 'bound to a different event loop'.  Clear it before each test."""
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
    except Exception:  # noqa: BLE001 - best effort, sse-starlette optional
        pass
