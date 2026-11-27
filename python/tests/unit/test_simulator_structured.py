"""Structured output on the local gateway simulator (BG §3.4, #231).

``structured_content`` builds a JSON document that validates against the schema
a request asked for, and a stock ``openai`` client's ``.parse()`` against the
in-process simulator returns the typed model. The plain happy path is prose, so
``.parse()`` could not succeed on it before this change.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Literal

import httpx
import pytest

pytest.importorskip("starlette")
openai = pytest.importorskip("openai")
pydantic = pytest.importorskip("pydantic")

from pydantic import BaseModel, Field  # noqa: E402 — after importorskip

from donkey_kit.simulator import build_app  # noqa: E402
from donkey_kit.simulator.app import SIMULATOR_HEADER  # noqa: E402
from donkey_kit.simulator.structured import structured_content  # noqa: E402

_BASE_URL = "http://sim.local"


class Colour(str, Enum):
    red = "red"
    blue = "blue"


class Address(BaseModel):
    street: str
    zip_code: int = Field(ge=10000)


class Ticket(BaseModel):
    id: str
    priority: Literal["low", "high"]
    colour: Colour
    tags: list[str] = Field(min_length=2)
    opened: dt.date
    created: dt.datetime
    address: Address | None
    score: float = Field(gt=0.5)
    urgent: bool
    note: str | None


class Left(BaseModel):
    right: Right  # resolved by model_rebuild below


class Right(BaseModel):
    left: Left


Left.model_rebuild()


def _response_format(model: type[BaseModel]) -> dict[str, Any]:
    """The ``response_format`` the openai client sends for ``.parse(response_format=model)``."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": model.model_json_schema(),
        },
    }


def test_document_validates_against_the_requested_model() -> None:
    content = structured_content(_response_format(Ticket))
    assert content is not None
    ticket = Ticket.model_validate_json(content)  # raises if not schema-valid
    assert ticket.priority in {"low", "high"}
    assert ticket.address is not None  # Address | None picks the non-null branch


@pytest.mark.parametrize(
    "response_format",
    [
        None,
        {"type": "text"},
        {"type": "json_object"},
        {"type": "json_schema"},
        {"type": "json_schema", "json_schema": {"name": "no-schema"}},
    ],
)
def test_no_structured_output_is_requested(response_format: Any) -> None:
    assert structured_content(response_format) is None


def test_unresolvable_or_non_local_ref_falls_back_to_plain_prose() -> None:
    external = {"type": "json_schema", "json_schema": {"schema": {"$ref": "https://x/y.json"}}}
    dangling = {"type": "json_schema", "json_schema": {"schema": {"$ref": "#/$defs/Missing"}}}
    assert structured_content(external) is None
    assert structured_content(dangling) is None


def test_mutually_required_recursion_falls_back_to_plain_prose() -> None:
    # Left needs Right and Right needs Left: no finite document exists.
    assert structured_content(_response_format(Left)) is None


def _stock_client() -> openai.AsyncOpenAI:
    """A stock ``openai.AsyncOpenAI`` whose transport is the in-process simulator."""
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app()), base_url=_BASE_URL
    )
    return openai.AsyncOpenAI(
        api_key="unused-by-the-simulator",
        base_url=_BASE_URL,  # no /v1 segment, as for the proxy
        max_retries=0,
        http_client=http_client,
        default_headers={"client_id": "sim", "client_secret": "sim"},
    )


async def test_stock_parse_returns_the_typed_model_and_keeps_the_honesty_stamp() -> None:
    client = _stock_client()
    async with client:
        raw = await client.chat.completions.with_raw_response.parse(
            model="gpt-4o",
            messages=[{"role": "user", "content": "make a ticket"}],
            response_format=Ticket,
        )
        completion = raw.parse()
    assert raw.headers.get(SIMULATOR_HEADER) == "true"
    parsed = completion.choices[0].message.parsed
    assert isinstance(parsed, Ticket)


async def test_plain_create_still_serves_the_captured_prose() -> None:
    client = _stock_client()
    async with client:
        completion = await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
    assert completion.choices[0].message.content == "pong — how can I help?"
