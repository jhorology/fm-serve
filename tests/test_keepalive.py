import asyncio
import time

from fastapi.testclient import TestClient

from fm_serve.keepalive import KeepAliveManager
from fm_serve.proxy import create_app


def test_keepalive_initial_state():
    km = KeepAliveManager(timeout_sec=10.0, enabled=False)
    assert km.timeout_sec == 10.0
    assert km.remaining_sec <= 10.0
    assert km.is_active is False
    status = km.status()
    assert status["active"] is False
    assert status["timeout_sec"] == 10


def test_keepalive_touch_updates_activity():
    km = KeepAliveManager(timeout_sec=10.0, enabled=False)
    km.last_activity = time.monotonic() - 5.0
    assert km.remaining_sec <= 6.0
    km.touch()
    assert km.remaining_sec >= 9.0


def test_keepalive_monitor_loop_releases():
    async def run():
        km = KeepAliveManager(timeout_sec=0.1, enabled=False)
        km._assertion_ids = [123]
        km._release_fn = lambda aid: 0
        km.last_activity = time.monotonic() - 0.2

        task = asyncio.create_task(km.monitor_loop(check_interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        assert km.is_active is False

    asyncio.run(run())


def test_health_includes_keepalive():
    # create_app without real backend: health will be degraded but still return keepalive
    app = create_app(
        backend_base="http://127.0.0.1:9",
        keepalive_timeout=1800.0,
        enable_keepalive=False,
        fm_process=None,
    )
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "fm-serve"
        assert "keepalive" in data
        assert data["keepalive"]["timeout_sec"] == 1800


def test_api_request_touches_keepalive():
    app = create_app(
        backend_base="http://127.0.0.1:9",
        keepalive_timeout=3600.0,
        enable_keepalive=False,
        fm_process=None,
    )
    with TestClient(app) as client:
        km: KeepAliveManager = app.state.keepalive
        km.last_activity = time.monotonic() - 100.0
        old_remaining = km.remaining_sec

        client.get("/health")
        assert km.remaining_sec <= old_remaining

        # /v1/models will 502 against dead backend, but middleware still touches
        client.get("/v1/models")
        assert km.remaining_sec > old_remaining + 50
