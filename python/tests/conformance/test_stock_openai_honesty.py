"""A **stock** ``openai.OpenAI`` client sees byte-identical shapes (BG §1.4, #189).

The honesty guarantee is only credible if it holds for the client a real user
runs — a plain ``openai.OpenAI`` pointed at the simulator, *not* the SDK, which
could otherwise be quietly normalising the bytes on the way in or out. So this
test drives the stock client against the simulator (in-process over
``httpx.ASGITransport`` — no TCP port) and asserts, at the transport layer, that
the **status, body bytes and discriminator/honesty headers** the client
actually received equal the committed fixture — the raw capture, never the
SDK's parsed view.

``importorskip``-guarded on ``openai`` (the ``[llm]`` extra) and ``starlette``
(the ``[local]`` extra), so the base-only CI job skips it cleanly.
"""

from __future__ import annotations

import httpx
import pytest

openai = pytest.importorskip("openai")
pytest.importorskip("starlette")

from donkey_kit.simulator import build_app  # noqa: E402 — after importorskip
from donkey_kit.simulator.app import SIMULATOR_HEADER  # noqa: E402
from donkey_kit.simulator.fixtures import Fixture, load, replay_headers  # noqa: E402

# Every rejection the simulator can be forced to serve via the model-id sentinel,
# plus the consumer-auth 401 — the shapes whose byte-fidelity the honesty
# guarantee is about.
REJECTION_SHAPES = [
    "token-rate-limit",
    "pii-detected",
    "injection-protection",
    "regex-prompt-guard",
    "content-safety",
    "content-moderation",
    "model-not-found",
    "upstream-5xx",
    "client-id-missing",
]

_BASE_URL = "http://sim.local"


def _stock_client() -> object:
    """A stock ``openai.AsyncOpenAI`` whose transport is the in-process simulator
    (``httpx.ASGITransport`` — no TCP port, no uvicorn; the async client is
    required because ``ASGITransport`` is async-only).

    ``max_retries=0`` so the client does not silently retry the 429/503 shapes
    (which would slow the test and hide the first response)."""
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app()),
        base_url=_BASE_URL,
    )
    return openai.AsyncOpenAI(
        api_key="unused-by-the-simulator",
        base_url=_BASE_URL,  # no /v1 segment — the proxy (and simulator) has none
        max_retries=0,
        http_client=http_client,
        default_headers={"client_id": "sim", "client_secret": "sim"},
    )


def _assert_matches_fixture(response: httpx.Response, fixture: Fixture) -> None:
    """The transport-level response is byte-identical to the captured fixture."""
    assert response.status_code == fixture.status
    assert response.content == fixture.body  # raw bytes, not the SDK's parsed view
    # The non-negotiable honesty marker on every simulator response.
    assert response.headers.get(SIMULATOR_HEADER) == "true"
    # Every discriminator/semantic header the fixture carries is replayed verbatim.
    for key, value in replay_headers(fixture).items():
        assert response.headers.get(key) == value


@pytest.mark.parametrize("shape", REJECTION_SHAPES)
async def test_stock_openai_client_sees_byte_identical_rejections(shape: str) -> None:
    client = _stock_client()
    async with client:
        with pytest.raises(openai.APIStatusError) as excinfo:
            await client.responses.create(model=f"donkey-sim/{shape}", input="ping")
    _assert_matches_fixture(excinfo.value.response, load(shape))


async def test_stock_openai_client_sees_the_honesty_header_on_the_happy_path() -> None:
    """Even the successful 200 carries ``x-donkey-simulator: true`` and replays
    the captured success body byte-for-byte."""
    client = _stock_client()
    async with client:
        raw = await client.responses.with_raw_response.create(
            model="gpt-5.1", input="ping"
        )
    http_response = raw.http_response
    assert http_response.status_code == 200
    assert http_response.headers.get(SIMULATOR_HEADER) == "true"
    assert http_response.content == load("success").body
