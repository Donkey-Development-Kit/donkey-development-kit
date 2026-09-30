"""``connection_kwargs()`` and the other public credential mappings print masked
but behave exactly like the plain dicts they replace.

``connection_kwargs()`` is the whole supported surface for seven of the eight
adapters (BG §1.8), and it carries the proxy's consumer secret in a nested header
mapping (``default_headers`` / ``extra_headers`` / ``client_args`` …). These
tests pin two things for every adapter in the ``ADAPTERS`` registry — so a new
adapter is covered automatically — plus ADK's Gemini kwargs and
``core.proxy_auth_headers()``:

* rendering (``repr``, ``str``, f-strings, ``pprint``) never shows a secret value,
  while every non-secret key and value stays readable;
* behaviour is unchanged: ``**`` unpacking, ``dict()``, lookups, ``==``,
  ``isinstance(…, dict)``, ``copy``/``deepcopy`` and ``json.dumps`` do what they
  do on the equivalent plain dict, and the real values reach the framework.
"""

from __future__ import annotations

import copy
import importlib
import json
import pprint
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient, proxy_auth_headers
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter
from donkey_kit.llm.client import LLMClient

_PROXY_SECRET = "proxy-secret-value"
_PROXY_KEY = "proxy-key-value"
_CP_SECRET = "cp-secret-value"
_SECRETS = (_PROXY_SECRET, _PROXY_KEY, _CP_SECRET)


def _cfg(**kw: Any) -> DonkeyConfig:
    return DonkeyConfig(
        client_id="cp-client-id",
        client_secret=_CP_SECRET,
        llm_proxy_url="https://proxy.example.internal",
        llm_proxy_client_id="proxy-client-id",
        llm_proxy_client_secret=_PROXY_SECRET,
        llm_proxy_key=_PROXY_KEY,
        **kw,
    )


def _adapter(name: str, cfg: DonkeyConfig | None = None) -> Adapter:
    spec = ADAPTERS[name]
    cls = getattr(importlib.import_module(spec.module, "donkey_kit.integrations"), spec.cls)
    cfg = cfg or _cfg()
    adapter: Adapter = cls(cfg, DonkeyAsyncClient(cfg, None))
    return adapter


def _adapter_kwargs(name: str) -> Callable[[], Mapping[str, Any]]:
    def build() -> Mapping[str, Any]:
        try:
            return _adapter(name).connection_kwargs()  # type: ignore[attr-defined, no-any-return]
        except ImportError as exc:  # openai_agents needs the [llm] extra
            pytest.skip(f"{name}.connection_kwargs() needs an optional extra: {exc}")

    return build


def _gemini_kwargs() -> Mapping[str, Any]:
    from donkey_kit.integrations.adk import ADKAdapter

    cfg = _cfg()
    return ADKAdapter(cfg, DonkeyAsyncClient(cfg, None)).gemini_connection_kwargs()


#: Every public accessor that returns a mapping holding a proxy credential.
_SURFACES: dict[str, Callable[[], Mapping[str, Any]]] = {
    **{f"{name}.connection_kwargs": _adapter_kwargs(name) for name in ADAPTERS},
    "adk.gemini_connection_kwargs": _gemini_kwargs,
    "core.proxy_auth_headers": lambda: proxy_auth_headers(_cfg()),
}

_RENDERERS: dict[str, Callable[[object], str]] = {
    "repr": repr,
    "str": str,
    "f-string": lambda x: f"{x}",
    "format-spec": lambda x: f"{x!s:>1}",
    "pformat": pprint.pformat,
    "pformat-narrow": lambda x: pprint.pformat(x, width=20),
    "nested-in-plain-dict": lambda x: repr({"kwargs": x}),
    "pformat-nested": lambda x: pprint.pformat({"kwargs": [x]}),
}


def _plain(value: Any) -> Any:
    """The same structure with every mapping turned into a plain ``dict``."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _leaves(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        out: list[tuple[tuple[str, ...], Any]] = []
        for k, v in value.items():
            out += _leaves(v, (*path, str(k)))
        return out
    return [(path, value)]


_SURFACE_IDS = list(_SURFACES)


@pytest.fixture(params=_SURFACE_IDS)
def kwargs(request: pytest.FixtureRequest) -> Mapping[str, Any]:
    return _SURFACES[request.param]()


# --- rendering ---------------------------------------------------------------------


@pytest.mark.parametrize("render", list(_RENDERERS.values()), ids=list(_RENDERERS))
def test_rendering_omits_secret_values(
    kwargs: Mapping[str, Any], render: Callable[[object], str]
) -> None:
    text = render(kwargs)
    for secret in _SECRETS:
        assert secret not in text


def test_the_mapping_really_carries_a_secret(kwargs: Mapping[str, Any]) -> None:
    # Precondition for the rendering test: there is a real secret to hide, except
    # where the credential lives inside a pre-built native client.
    values = [v for _path, v in _leaves(kwargs)]
    if "openai_client" in kwargs:
        assert kwargs["openai_client"].api_key == _PROXY_KEY
    else:
        assert _PROXY_SECRET in values


def test_rendering_keeps_non_secret_keys_and_values(kwargs: Mapping[str, Any]) -> None:
    text = repr(kwargs)
    for path, value in _leaves(kwargs):
        assert repr(path[-1]) in text  # every key name, secret or not
        if isinstance(value, (str, int, bool)) and value not in _SECRETS:
            assert repr(value) in text


def test_proxy_url_and_client_id_stay_visible() -> None:
    kw = _SURFACES["langgraph.connection_kwargs"]()
    text = repr(kw)
    assert "https://proxy.example.internal" in text
    assert "proxy-client-id" in text


# --- unchanged behaviour ----------------------------------------------------------------


def test_is_a_dict_equal_to_its_plain_twin(kwargs: Mapping[str, Any]) -> None:
    assert isinstance(kwargs, dict)
    assert kwargs == _plain(kwargs)
    assert dict(kwargs) == _plain(kwargs)


def test_lookups_return_the_real_values(kwargs: Mapping[str, Any]) -> None:
    plain = _plain(kwargs)
    for key in kwargs:
        assert kwargs[key] == plain[key]
    for path, value in _leaves(kwargs):
        node: Any = kwargs
        for key in path:
            node = node[key]
        assert node == value


def test_star_star_unpacking_passes_the_real_values(kwargs: Mapping[str, Any]) -> None:
    def spread(**kw: Any) -> dict[str, Any]:
        return kw

    assert spread(**kwargs) == _plain(kwargs)


def _same_outcome(op: Callable[[Any], Any], masked: Any, plain: Any) -> None:
    """``op`` behaves on ``masked`` exactly as on the plain twin: same result, or
    the same exception type."""
    try:
        expected = op(plain)
    except Exception as exc:  # noqa: BLE001 - asserting parity, whatever it is
        with pytest.raises(type(exc)):
            op(masked)
        return
    assert op(masked) == expected


def test_copy_deepcopy_and_json_match_a_plain_dict(kwargs: Mapping[str, Any]) -> None:
    plain = _plain(kwargs)
    _same_outcome(copy.copy, kwargs, plain)
    _same_outcome(copy.deepcopy, kwargs, plain)
    _same_outcome(json.dumps, kwargs, plain)


def test_json_dumps_emits_the_real_values() -> None:
    kw = _SURFACES["llamaindex.connection_kwargs"]()
    assert json.loads(json.dumps(kw))["default_headers"]["client_secret"] == _PROXY_SECRET


@pytest.mark.parametrize(
    "duplicate",
    [copy.copy, copy.deepcopy, lambda x: x.copy(), lambda x: x | {"extra": 1}],
    ids=["copy.copy", "copy.deepcopy", ".copy()", "| merge"],
)
def test_copies_stay_masked(duplicate: Callable[[Any], Any]) -> None:
    kw = _SURFACES["llamaindex.connection_kwargs"]()  # data-only, so deepcopy works
    dup = duplicate(kw)
    assert dup["default_headers"]["client_secret"] == _PROXY_SECRET
    for secret in _SECRETS:
        assert secret not in repr(dup)
        assert secret not in pprint.pformat(dup)


def test_explicit_dict_conversion_is_the_plain_view() -> None:
    # dict() is the documented way to get an unmasked mapping, e.g. for a
    # framework that insists on an exact dict.
    kw = _SURFACES["llamaindex.connection_kwargs"]()
    assert type(dict(kw)) is dict


# --- the real values reach the framework ------------------------------------------------


class _HeaderModel(BaseModel):
    """Stands in for pydantic-validated framework kwargs (LangChain, google-genai)."""

    default_headers: dict[str, str]
    api_key: str


def test_pydantic_coercion_keeps_the_real_values() -> None:
    kw = _SURFACES["agent_framework.connection_kwargs"]()
    model = _HeaderModel(**kw)
    assert model.default_headers["client_secret"] == _PROXY_SECRET
    assert model.api_key == _PROXY_KEY


@pytest.mark.parametrize("name", ["agent_framework", "llamaindex"])
def test_native_openai_client_built_from_kwargs_gets_the_secret(name: str) -> None:
    openai = pytest.importorskip("openai")
    kw = _SURFACES[f"{name}.connection_kwargs"]()
    base_url = kw.get("base_url") or kw["api_base"]
    client = openai.AsyncOpenAI(
        base_url=base_url, api_key=kw["api_key"], default_headers=kw["default_headers"]
    )
    assert client.default_headers["client_secret"] == _PROXY_SECRET
    assert client.api_key == _PROXY_KEY


def test_openai_agents_client_gets_the_secret() -> None:
    pytest.importorskip("openai")
    client = _SURFACES["openai_agents.connection_kwargs"]()["openai_client"]
    assert client.default_headers["client_secret"] == _PROXY_SECRET


def test_strands_client_args_build_a_client_that_sends_the_secret() -> None:
    openai = pytest.importorskip("openai")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"object": "list", "data": []})

    args = _SURFACES["strands.connection_kwargs"]()["client_args"]
    # Strands hands client_args to an OpenAI client; swap only the transport.
    sync_args = {k: v for k, v in args.items() if k != "http_client"}
    client = openai.OpenAI(
        **sync_args, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.models.list()
    assert seen["client_secret"] == _PROXY_SECRET


async def test_raw_llm_client_sends_the_secret_on_the_wire() -> None:
    pytest.importorskip("openai")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"object": "list", "data": []})

    cfg = _cfg()
    http = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    client = LLMClient(cfg, http).client()
    await client.models.list()
    assert seen["client_secret"] == _PROXY_SECRET
    assert seen["authorization"] == f"Bearer {_PROXY_KEY}"


# --- the shared list of sensitive names -------------------------------------------------


@pytest.mark.parametrize(
    "name", ["api_key", "client_secret", "Authorization", "authorization", "CLIENT_SECRET"]
)
def test_every_sensitive_name_is_masked_case_insensitively(name: str) -> None:
    from donkey_kit.core.masking import masked

    rendered = repr(masked({name: "Bearer hidden-value", "visible": "shown"}))
    assert "hidden-value" not in rendered
    assert "'shown'" in rendered


def test_sensitive_names_include_the_proxy_secret_and_jwt_headers() -> None:
    from donkey_kit.core.masking import SENSITIVE_NAMES

    from donkey_kit.core import _verify

    assert _verify.LLM_PROXY_CLIENT_SECRET_HEADER.lower() in SENSITIVE_NAMES
    assert _verify.LLM_PROXY_WALLET_JWT_HEADER.lower() in SENSITIVE_NAMES
    assert _verify.LLM_PROXY_CLIENT_ID_HEADER.lower() not in SENSITIVE_NAMES
