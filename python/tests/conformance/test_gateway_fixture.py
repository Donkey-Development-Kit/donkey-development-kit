"""End-to-end tests for the out-of-process ``gateway`` fixture (#278, `BG §1.5`).

These boot the real simulator on an ephemeral port and drive it with a **stock
``httpx`` client** — deliberately not through ``donkey_kit`` — because the whole
point of the fixture is testing a subject that does not import the SDK. They are
marked ``local_gateway`` (deselected by default) and skip cleanly without the
``[local]`` extra.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit.conformance.gateway import start_gateway
from donkey_kit.simulator.app import SIM_MODEL_PREFIX, SIMULATOR_HEADER

pytestmark = pytest.mark.local_gateway

# Skip the whole module (rather than error) when the server extra is absent, even
# under `pytest -m local_gateway`.
pytest.importorskip("uvicorn")
pytest.importorskip("starlette")


def test_url_is_reachable_by_a_stock_client_and_is_honesty_stamped(gateway: object) -> None:
    # A process that never imports donkey_kit points a plain httpx client at the
    # URL and hits the simulator exactly like the real proxy.
    url = gateway.url  # type: ignore[attr-defined]
    assert url.startswith("http://127.0.0.1:")
    resp = httpx.post(
        f"{url}/responses",
        json={"model": "gpt-5.1", "input": "hello"},
        headers={"client_id": "pub", "client_secret": "shh"},
    )
    assert resp.status_code == 200
    assert resp.headers[SIMULATOR_HEADER] == "true"


def test_requests_received_counts_and_redacts_the_secret(gateway: object) -> None:
    url = gateway.url  # type: ignore[attr-defined]
    for _ in range(3):
        httpx.post(
            f"{url}/responses",
            json={"model": "gpt-5.1", "input": "hi"},
            headers={"client_id": "pub", "client_secret": "super-secret"},
        )
    assert gateway.requests_received == 3  # type: ignore[attr-defined]
    recorded = gateway.requests  # type: ignore[attr-defined]
    assert all(r.method == "POST" and r.path == "/responses" for r in recorded)
    # The secret never survives into the spy, even over the wire.
    assert recorded[0].headers["client_id"] == "pub"
    assert recorded[0].headers["client_secret"] == "***"


def test_scenarios_are_configurable_per_test(gateway: object) -> None:
    # Per-test #188 config: arm a scenario live, no restart, no per-process flag.
    gateway.set_scenarios("pii_block:every=1")  # type: ignore[attr-defined]
    url = gateway.url  # type: ignore[attr-defined]
    resp = httpx.post(f"{url}/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert resp.status_code == 403  # pii-detected fixture
    assert resp.headers[SIMULATOR_HEADER] == "true"
    # The request is still recorded even though it was rejected.
    assert gateway.requests_received == 1  # type: ignore[attr-defined]


def test_reset_clears_the_request_log(gateway: object) -> None:
    url = gateway.url  # type: ignore[attr-defined]
    httpx.post(f"{url}/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert gateway.requests_received == 1  # type: ignore[attr-defined]
    gateway.reset()  # type: ignore[attr-defined]
    assert gateway.requests_received == 0  # type: ignore[attr-defined]


def test_model_id_sentinel_forces_a_rejection_shape(gateway: object) -> None:
    # The simulator's fixed sentinel works over the real port too (no scenario).
    url = gateway.url  # type: ignore[attr-defined]
    resp = httpx.post(
        f"{url}/responses", json={"model": f"{SIM_MODEL_PREFIX}pii-detected", "input": "hi"}
    )
    assert resp.status_code == 403


def test_two_gateways_bind_distinct_ephemeral_ports() -> None:
    # Ephemeral binding is what lets parallel `pytest -n` runs never collide.
    a = start_gateway()
    b = start_gateway()
    try:
        assert a.url != b.url
    finally:
        a.close()
        b.close()
