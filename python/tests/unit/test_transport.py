"""Transport: header injection, retry policy, no-retry-on-policy-rejection,
401 refresh (BG §1.1). Uses httpx MockTransport so no network is touched."""

from __future__ import annotations

import asyncio
import warnings

import httpx
import pytest

from donkey_kit.core import telemetry
from donkey_kit.core._verify import UnverifiedValueWarning
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.budget import Budget
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import GatewayUnavailable, PIIDetected, classify
from donkey_kit.core.lastcall import LastCallStatus, current_last_call
from donkey_kit.core.telemetry import current_correlation_id, run_context, run_scope
from donkey_kit.core.transport import (
    CALL_ID_HEADER,
    CORRELATION_HEADER,
    DonkeyAsyncClient,
    DonkeyClient,
    attribution_headers,
    cost_headers,
    effective_cost_tags,
    proxy_auth_headers,
)


def _client(handler, cfg=None, auth=None) -> DonkeyAsyncClient:
    c = DonkeyAsyncClient(cfg or DonkeyConfig(), auth, transport=httpx.MockTransport(handler))
    return c


def _sync_client(handler, cfg=None) -> DonkeyClient:
    return DonkeyClient(cfg or DonkeyConfig(), transport=httpx.MockTransport(handler))


class _RecordingAsync(DonkeyAsyncClient):
    """Overrides the logical hooks with counters, to assert call-once semantics."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests = 0
        self.responses: list[int] = []

    async def _on_request(self, request: httpx.Request) -> None:
        self.requests += 1

    async def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        self.responses.append(response.status_code)


class _RecordingSync(DonkeyClient):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests = 0
        self.responses: list[int] = []

    def _on_request(self, request: httpx.Request) -> None:
        self.requests += 1

    def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        self.responses.append(response.status_code)


async def test_correlation_and_attribution_headers_injected() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = DonkeyConfig(application_name="hr-agent", business_group="finance")
    async with _client(handler, cfg) as client:
        # The correlation header name is VERIFIED as of #522, so the warning here
        # comes from the still-UNVERIFIED application/business-group attribution
        # placeholder names (verification discipline).
        with pytest.warns(UnverifiedValueWarning):
            with run_context("run-1"):
                await client.get("https://x/thing")

    assert seen[CORRELATION_HEADER.lower()] == "run-1"
    # Attribution header values present (names are unverified placeholders).
    assert "hr-agent" in seen.values()
    assert "finance" in seen.values()


def test_proxy_auth_headers_carry_verified_client_id_secret() -> None:
    """docs/verified-apis.md §2/§3 (LIVE): the direct-proxy auth is a client_id/client_secret
    request-header pair — verified names, no warning, no bearer."""
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )
    headers = proxy_auth_headers(cfg)
    assert headers["client_id"] == "cid"
    assert headers["client_secret"] == "csecret"
    assert "Authorization" not in headers  # NOT a bearer credential


def test_proxy_auth_headers_omit_absent_credentials() -> None:
    assert proxy_auth_headers(DonkeyConfig(llm_proxy_url="https://proxy")) == {}


# --- jwt / model-wallet auth mode (BG §1.1, #372/#509) ----------------------
# The wallet ingress disables Client ID Enforcement: no client_id/client_secret
# pair, a durable wallet-selector X-Client-Id, and a rotating JWT injected
# per-send from the attached AuthProvider (never a config field).


class _RotatingToken:
    """An AuthProvider that hands out a fresh token on every ``token()`` call, so
    a test can assert the transport reads it per-send rather than pinning once."""

    def __init__(self) -> None:
        self.n = 0

    async def token(self) -> str:
        self.n += 1
        return f"jwt-{self.n}"

    async def invalidate(self) -> None:
        self.n += 1  # a refresh advances the token, like a real IdP rotation


def _jwt_cfg(**kw) -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_auth="jwt",
        llm_proxy_url="https://proxy",
        llm_proxy_wallet_client_id="wallet-42",
        **kw,
    )


def test_proxy_auth_headers_jwt_mode_carries_wallet_selector_not_cie() -> None:
    """jwt mode (#509): the snapshot carries only the durable X-Client-Id
    wallet selector — never the CIE pair (Client ID Enforcement is disabled),
    and never the rotating JWT (that is injected per-send, not snapshotted)."""
    headers = proxy_auth_headers(_jwt_cfg())
    assert headers["X-Client-Id"] == "wallet-42"
    assert "client_id" not in headers
    assert "client_secret" not in headers
    assert "Authorization" not in headers


async def test_jwt_mode_injects_fresh_bearer_overriding_preset() -> None:
    """The OpenAI SDK pre-sets Authorization from its mandatory api_key slot; a
    wallet proxy READS that header as the JWT, so the transport must OVERRIDE the
    preset with the fresh per-send token, and stamp the wallet selector too."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    async with _client(handler, _jwt_cfg(), _RotatingToken()) as client:
        # Simulate the OpenAI SDK's pre-set sentinel bearer on the request.
        await client.get("https://x", headers={"Authorization": "Bearer sentinel"})

    assert seen["authorization"] == "Bearer jwt-1"  # overridden, not the sentinel
    assert seen["x-client-id"] == "wallet-42"


async def test_jwt_token_refreshed_per_send() -> None:
    """A rotating JWT is read from the provider on every send, so a second
    request carries the next token — the refresh the wallet ingress needs."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200)

    async with _client(handler, _jwt_cfg(), _RotatingToken()) as client:
        await client.get("https://x")
        await client.get("https://x")

    assert seen == ["Bearer jwt-1", "Bearer jwt-2"]


async def test_jwt_401_refreshes_and_retries_once() -> None:
    """A 401 invalidates the provider and re-sends once with the refreshed token
    (BG §1.1) — the same loop the CIE path uses, now carrying the wallet JWT."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if len(seen) == 1 else httpx.Response(200)

    async with _client(handler, _jwt_cfg(), _RotatingToken()) as client:
        resp = await client.get("https://x")

    assert resp.status_code == 200
    # First send jwt-1 (401) → invalidate advances → re-send with a fresh token.
    assert seen[0] == "Bearer jwt-1"
    assert seen[1] != seen[0]


async def test_client_id_mode_authorization_setdefault_preserves_preset() -> None:
    """In the DEFAULT client-id mode the CIE proxy ignores Authorization, so the
    transport must NOT clobber a caller-set bearer — setdefault, not override.
    (The override behaviour above is jwt-mode-only.)"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    # Default client-id mode, but a token provider is attached (control-plane).
    async with _client(handler, DonkeyConfig(), StaticToken("cp-token")) as client:
        await client.get("https://x", headers={"Authorization": "Bearer preset"})

    assert seen["authorization"] == "Bearer preset"  # preserved, not overridden


async def test_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 3


async def test_negative_retry_after_does_not_crash_the_retry() -> None:
    # A 503 with a negative Retry-After must retry immediately (delay floored to
    # 0), not raise ValueError out of asyncio.sleep(-1.0) (#286).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, headers={"retry-after": "-1"})
        return httpx.Response(200)

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_does_not_retry_4xx_policy_rejection() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 400
    assert calls["n"] == 1  # terminal — NOT retried (BG §1.2)


async def test_401_triggers_single_token_refresh() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200)

    async with _client(handler, DonkeyConfig(), StaticToken("t")) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 2  # refreshed once, retried once (BG §1.1)


# --- blocking twin (client(sync=True)) ------------------------------------
# The point of DonkeyClient is that a synchronous caller is governed on exactly
# the same terms, so these mirror the async cases above.


def test_sync_correlation_and_attribution_headers_injected() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = DonkeyConfig(application_name="hr-agent", business_group="finance")
    # No pytest.warns here: _verify warns once per key per process, so the async
    # case above has already consumed it. That contract is asserted there.
    with _sync_client(handler, cfg) as client:
        with run_context("run-1"):
            client.get("https://x/thing")

    assert seen[CORRELATION_HEADER.lower()] == "run-1"
    assert "hr-agent" in seen.values()
    assert "finance" in seen.values()


def test_sync_requests_do_not_pin_a_correlation_id_to_the_process() -> None:
    """A blocking call outside run_context() must not bind its ID to the ambient
    context: doing so would make every later unrelated call report the same run.
    Async gets away with binding because asyncio.run() isolates the Context."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers[CORRELATION_HEADER])
        return httpx.Response(200)

    with _sync_client(handler) as client:
        client.get("https://x")
        client.get("https://x")

    assert seen[0] != seen[1]  # each call is its own run
    assert current_correlation_id() is None  # nothing leaked out


def test_sync_requests_share_one_id_inside_a_run_context() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers[CORRELATION_HEADER])
        return httpx.Response(200)

    with _sync_client(handler) as client, run_context("run-7"):
        client.get("https://x")
        client.get("https://x")

    assert seen == ["run-7", "run-7"]


# --- the two ids: run correlation id vs per-call id (BG §1.1, #195) ------------
# X-Correlation-Id groups a run (shared); X-Donkey-Request-Id pinpoints one
# request within it (unique, but stable across that request's own retries).


async def test_call_id_is_unique_per_call_and_distinct_from_run_id() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.headers[CORRELATION_HEADER], request.headers[CALL_ID_HEADER])
        )
        return httpx.Response(200)

    async with _client(handler) as client:
        with run_context("run-42"):
            await client.get("https://x/a")
            await client.get("https://x/b")

    # Same run id on both calls; a fresh, distinct call id each time.
    assert seen[0][0] == seen[1][0] == "run-42"
    assert seen[0][1] != seen[1][1]
    assert seen[0][1] != "run-42" and seen[1][1] != "run-42"


async def test_call_id_is_stable_across_retries() -> None:
    """A retried request is ONE logical call: its call id must not change between
    the 503 attempts and the eventual 200, so the whole retry chain shares a call
    id while the run id also stays put."""
    calls = {"n": 0}
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        seen.append(
            (request.headers[CORRELATION_HEADER], request.headers[CALL_ID_HEADER])
        )
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        with run_context("run-r"):
            await client.get("https://x")

    assert calls["n"] == 3  # two retries then success
    assert {c for c, _ in seen} == {"run-r"}  # run id constant
    assert len({call for _, call in seen}) == 1  # ONE call id across all sends


async def test_config_overrides_correlation_and_call_id_header_names() -> None:
    """A customer whose gateway reads different inbound names points the SDK at
    them via config; the override is used verbatim (verification discipline escape hatch, #195)."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = DonkeyConfig(correlation_header="X-Trace-Id", call_id_header="X-Req-Seq")
    async with _client(handler, cfg) as client:
        with run_context("run-ovr"):
            await client.get("https://x")

    assert seen["x-trace-id"] == "run-ovr"
    assert "x-req-seq" in seen
    # The default placeholder names are NOT also sent when overridden.
    assert CORRELATION_HEADER.lower() not in seen
    assert CALL_ID_HEADER.lower() not in seen


# A minimal PII refusal body — classify() maps 403 + type "pii_detected" (and no
# www-authenticate) to PIIDetected; every branch sets correlation_id/call_id from
# the request, so this is enough to exercise the #363 read-back.
_PII_REFUSAL = {"error": {"type": "pii_detected", "message": "PII detected."}}


async def test_overridden_header_names_survive_classify_round_trip() -> None:
    """#363: with the header names overridden, the ids the transport SENT are the
    ids ``classify()`` reads back — not ``None`` because ``_sent_ids`` looked up
    the default placeholder names. The transport stamps the resolved names on
    ``request.extensions``; read-back honours them without importing config.

    This is the round-trip companion the existing override test above never made:
    it asserted only that the configured names are *sent*."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200)

    cfg = DonkeyConfig(correlation_header="X-Trace-Id", call_id_header="X-Req-Seq")
    async with _client(handler, cfg) as client:
        with run_context("run-42"):
            await client.get("https://x")

    request = captured["req"]
    # A refusal carrying that same request, exactly as ``response.request`` would.
    err = classify(httpx.Response(403, json=_PII_REFUSAL, request=request))
    assert isinstance(err, PIIDetected)
    assert err.correlation_id == "run-42"  # was None before #363
    assert err.call_id == request.headers["x-req-seq"]  # the id actually on the wire


async def test_default_header_names_survive_classify_round_trip() -> None:
    """#363 AC3: the no-override default path is unchanged — the stamped name is
    the placeholder name, so read-back still matches."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200)

    async with _client(handler) as client:  # no override
        with run_context("run-def"):
            await client.get("https://x")

    request = captured["req"]
    err = classify(httpx.Response(403, json=_PII_REFUSAL, request=request))
    assert err.correlation_id == "run-def"
    assert err.call_id == request.headers[CALL_ID_HEADER.lower()]


async def test_classify_round_trip_emits_no_unverified_warning() -> None:
    """#363 AC4 / verification discipline: read-back must never emit an ``UnverifiedValueWarning`` —
    that warning belongs at injection time. The extensions fallback touches
    ``.placeholder``, never ``Unverified.get()``. Covers both the stamped path
    and a response with no stamp at all (a hand-built stock-client response)."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200)

    async with _client(handler) as client:
        with run_context("run-w"):
            await client.get("https://x")

    request = captured["req"]
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        # Stamped path.
        classify(httpx.Response(403, json=_PII_REFUSAL, request=request))
        # No-stamp path: a bare request the SDK's transport never touched.
        bare = httpx.Request("POST", "https://x")
        classify(httpx.Response(403, json=_PII_REFUSAL, request=bare))


def test_sync_overridden_header_names_survive_classify_round_trip() -> None:
    """#363 cross-surface lockstep: the sync ``DonkeyClient`` shares the same
    header-apply helpers, so the same read-back round trip holds."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200)

    cfg = DonkeyConfig(correlation_header="X-Trace-Id", call_id_header="X-Req-Seq")
    with _sync_client(handler, cfg) as client:
        with run_context("run-sync"):
            client.get("https://x")

    request = captured["req"]
    err = classify(httpx.Response(403, json=_PII_REFUSAL, request=request))
    assert err.correlation_id == "run-sync"
    assert err.call_id == request.headers["x-req-seq"]


async def test_concurrent_runs_do_not_leak_correlation_ids() -> None:
    """No leakage across concurrent runs (#195 AC): two runs racing on their own
    asyncio tasks each stamp ONLY their own run id on the wire, even though they
    share one DonkeyAsyncClient and interleave. The id is a contextvar, so each
    ``asyncio.gather`` child runs in its own copied context — one run cannot see
    the other's binding."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Echo the correlation id the client stamped so the caller can prove
        # which run id actually went on the wire for its own request.
        return httpx.Response(200, headers={"x-echo": request.headers[CORRELATION_HEADER]})

    async with _client(handler) as client:

        async def one_run(run_id: str) -> set[str]:
            seen: set[str] = set()
            with run_context(run_id):
                for _ in range(4):
                    resp = await client.get("https://x")
                    seen.add(resp.headers["x-echo"])
                    await asyncio.sleep(0)  # yield so the two runs interleave
            return seen

        a, b = await asyncio.gather(one_run("run-a"), one_run("run-b"))

    assert a == {"run-a"}  # run A only ever saw its own id on the wire
    assert b == {"run-b"}


async def test_run_id_reaches_a_request_fired_from_a_child_task() -> None:
    """The bound run id survives an asyncio task boundary (#195 AC): a request
    fired from a task spawned *inside* the run block still carries the run id,
    because the child task copies the bound context at creation — nothing is
    threaded through call arguments."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers[CORRELATION_HEADER])
        return httpx.Response(200)

    async with _client(handler) as client:
        with run_context("run-child"):
            await asyncio.create_task(client.get("https://x"))

    assert seen == ["run-child"]


def test_sync_call_id_is_stable_across_retries() -> None:
    calls = {"n": 0}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        seen.append(request.headers[CALL_ID_HEADER])
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        client.get("https://x")

    assert calls["n"] == 3
    assert len(set(seen)) == 1  # one call id across the retry chain


def test_sync_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_sync_negative_retry_after_does_not_crash_the_retry() -> None:
    # The sync twin shares _retry_delay, so it is equally affected: a negative
    # Retry-After must retry immediately, not raise ValueError from
    # time.sleep(-1.0) (#286).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, headers={"retry-after": "-1"})
        return httpx.Response(200)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_sync_does_not_retry_4xx_policy_rejection() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 400
    assert calls["n"] == 1  # terminal — NOT retried (BG §1.2)


def test_sync_401_is_terminal_because_there_is_no_token_to_refresh() -> None:
    """The async client retries a 401 once after refreshing. DonkeyClient takes
    no AuthProvider (async-only protocol), so a 401 is a real credential failure
    and must not be retried."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 401
    assert calls["n"] == 1


# --- lifecycle hooks (BG §1.1, #179) --------------------------------------
# The four seams every Phase 1 feature attaches to: _on_request / _on_response
# fire exactly once per logical send(); _on_refusal is defined but has no caller
# until classify() (#181); _transport is swappable on a live client.


async def test_default_hooks_are_noop_seams() -> None:
    """All four seams exist and the defaults are no-ops (byte-identical
    behaviour to a hookless client — the rest of this module asserts that)."""
    async with _client(lambda r: httpx.Response(200)) as client:
        req = httpx.Request("GET", "https://x")
        assert await client._on_request(req) is None
        assert await client._on_response(req, httpx.Response(200)) is None
        assert await client._on_refusal(None) is None


async def test_on_request_called_once_across_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    client = _RecordingAsync(
        DonkeyConfig(max_retries=3), None, transport=httpx.MockTransport(handler)
    )
    async with client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert client.requests == 1  # logical request hook fires once, not per wire retry
    assert client.responses == [200]  # response hook sees only the final response


async def test_hooks_fire_once_across_the_401_refresh_path() -> None:
    """The 401→refresh→retry path re-sends on the same logical send(), so the
    hooks must still fire exactly once — _on_response on the final 200, never on
    the intermediate 401."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200)

    client = _RecordingAsync(
        DonkeyConfig(), StaticToken("t"), transport=httpx.MockTransport(handler)
    )
    async with client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 2  # refreshed + retried once
    assert client.requests == 1  # request hook still fires once for the logical send
    assert client.responses == [200]  # never sees the intermediate 401


async def test_401_refresh_retries_even_on_the_final_attempt() -> None:
    """A 401 refresh is an auth re-send, not a rate-limit backoff, so it must
    always get its one retry independent of max_retries. Regression: with
    max_retries=0 the token was invalidated but the request was never re-sent,
    and the stale (closed) 401 was returned to the caller and to _on_response."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200)

    client = _RecordingAsync(
        DonkeyConfig(max_retries=0), StaticToken("t"), transport=httpx.MockTransport(handler)
    )
    async with client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 2  # refreshed + retried once, despite max_retries=0
    assert client.responses == [200]  # never the closed 401


async def test_on_response_not_called_when_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _RecordingAsync(DonkeyConfig(), None, transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(GatewayUnavailable) as ei:  # typed, wrapping the transport error (#379)
            await client.get("https://x")
    assert isinstance(ei.value.__cause__, httpx.ConnectError)  # original preserved
    assert client.requests == 1  # request hook ran before the send
    assert client.responses == []  # transport error is never masked by a response hook


async def test_swap_transport_takes_effect_on_the_next_request() -> None:
    async with _client(lambda r: httpx.Response(500)) as client:
        assert (await client.get("https://x")).status_code == 500  # 500 is non-retryable
        client._swap_transport(httpx.MockTransport(lambda r: httpx.Response(200)))
        assert (await client.get("https://x")).status_code == 200


def test_sync_on_request_called_once_across_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    client = _RecordingSync(DonkeyConfig(max_retries=3), transport=httpx.MockTransport(handler))
    with client:
        resp = client.get("https://x")
    assert resp.status_code == 200
    assert client.requests == 1
    assert client.responses == [200]


def test_sync_on_response_not_called_when_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _RecordingSync(DonkeyConfig(), transport=httpx.MockTransport(handler))
    with client:
        with pytest.raises(GatewayUnavailable) as ei:  # typed, wrapping the transport error (#379)
            client.get("https://x")
    assert isinstance(ei.value.__cause__, httpx.ConnectError)
    assert client.requests == 1
    assert client.responses == []


def test_sync_swap_transport_takes_effect_on_the_next_request() -> None:
    with _sync_client(lambda r: httpx.Response(500)) as client:
        assert client.get("https://x").status_code == 500
        client._swap_transport(httpx.MockTransport(lambda r: httpx.Response(200)))
        assert client.get("https://x").status_code == 200


# --- policy refusals are terminal: no retry (#183, BG §1.2) -------------------
# classify() maps EVERY 429 to TokenBudgetExceeded (a PolicyViolation), so a 429
# is terminal like any other policy refusal — retrying it just burns the same
# already-exhausted budget window (Scenario B: 50k records overnight). The
# transport is the SOLE retry authority (every OpenAI-SDK construction site sets
# max_retries=0), so the guarantee is proven here.


async def test_does_not_retry_429_budget_refusal() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)  # empty body, no retry-after (docs/verified-apis.md §4)

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 429
    assert calls["n"] == 1  # terminal on the first hit — never retried (BG §1.2, #183)


async def test_does_not_retry_403_policy_rejection() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)  # e.g. PII detected

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 403
    assert calls["n"] == 1


async def test_429_is_terminal_but_5xx_still_retries() -> None:
    """AC #3: the no-retry rule is status-specific, not a blanket disable. A
    transient 503 is still retried to exhaustion while a 429 budget refusal is
    terminal on the first hit."""
    n429 = {"n": 0}

    def h429(request: httpx.Request) -> httpx.Response:
        n429["n"] += 1
        return httpx.Response(429)

    async with _client(h429, DonkeyConfig(max_retries=2)) as client:
        await client.get("https://x")
    assert n429["n"] == 1  # terminal

    n503 = {"n": 0}

    def h503(request: httpx.Request) -> httpx.Response:
        n503["n"] += 1
        return httpx.Response(503)

    async with _client(h503, DonkeyConfig(max_retries=2)) as client:
        await client.get("https://x")
    assert n503["n"] == 3  # max_retries=2 → 3 attempts; 5xx stays retryable


def test_sync_does_not_retry_429_budget_refusal() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 429
    assert calls["n"] == 1


def test_sync_does_not_retry_403_policy_rejection() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 403
    assert calls["n"] == 1


# --- the guarantee holds through the native framework clients (AC #4) ------
# donkey.llm.client() and the adapters set the OpenAI SDK's own max_retries=0 and
# hand it our shared client, so the transport's no-429-retry is the whole story:
# a budget refusal reaches the wire exactly once.


async def test_openai_client_does_not_retry_429_end_to_end() -> None:
    openai = pytest.importorskip("openai")
    from donkey_kit.llm.client import LLMClient

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "budget exhausted"})

    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="sec",
        max_retries=3,
    )
    shared = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    async with shared:
        oai = LLMClient(cfg, shared).client()
        with pytest.raises(openai.APIStatusError):
            await oai.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
            )
    assert calls["n"] == 1  # SDK max_retries=0 + transport no-429-retry → one wire hit


async def test_langgraph_adapter_does_not_retry_429_end_to_end() -> None:
    pytest.importorskip("langchain_openai")
    openai = pytest.importorskip("openai")
    from donkey_kit.integrations.langgraph import LangGraphAdapter

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "budget exhausted"})

    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="sec",
        max_retries=3,
    )
    shared = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    adapter = LangGraphAdapter(cfg, shared)
    # Composition: the adapter disables the framework's own retry and hands it our
    # shared client, so the transport is what governs the retry policy.
    kw = adapter.connection_kwargs()
    assert kw["max_retries"] == 0
    assert kw["http_async_client"] is shared
    async with shared:
        model = adapter.chat_model("gpt-4o")
        with pytest.raises(openai.APIStatusError):
            await model.ainvoke("hi")
    assert calls["n"] == 1


# --- GenAI spans on the transport (#192, BG §1.6) --------------------------
# The transport is where the span is opened, because every governed call flows
# through send(). A GenAI request is a POST whose JSON body carries a `model`;
# GETs / token fetches / bodyless calls get no span, so all the tests above stay
# byte-identical. The span carries both the pinned gen_ai.* attributes and the
# stable donkey.* attributes on ONE span (AC #5). Wired to a real in-memory
# tracer here; skipped where the `otel` extra is not installed.

_PROVIDER_HEADER = "x-llm-proxy-llm-provider"
_LLM_CFG = DonkeyConfig(llm_proxy_url="https://proxy")


def _tracer_exporter():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("donkey_kit.test"), exporter


def _use_tracer(monkeypatch):
    tracer, exporter = _tracer_exporter()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)
    return exporter


_SUCCESS_BODY = {
    "model": "gpt-4o-2024-05-13",
    "usage": {"input_tokens": 1420, "output_tokens": 310, "total_tokens": 1730},
}


async def test_llm_post_emits_one_span_with_both_namespaces(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={_PROVIDER_HEADER: "openai", "x-token-remaining": "18450"},
            json=_SUCCESS_BODY,
        )

    budget = Budget()
    client = DonkeyAsyncClient(
        _LLM_CFG, None, budget=budget, transport=httpx.MockTransport(handler)
    )
    # run_context is a sync CM (it binds a contextvar); nest it outside the async
    # client so the correlation ID is bound for the duration of the post().
    with run_context("run-7f3a"):
        async with client:
            resp = await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
    assert resp.status_code == 200

    (span,) = exporter.get_finished_spans()  # exactly ONE span (AC #5)
    assert span.name == telemetry.SPAN_LLM_CHAT
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"  # from the REQUEST body
    assert attrs["gen_ai.system"] == "openai"  # from the verified provider header
    assert attrs["gen_ai.usage.input_tokens"] == 1420
    assert attrs["gen_ai.usage.output_tokens"] == 310
    assert attrs["donkey.policy.decision"] == "allow"
    assert attrs["donkey.budget.remaining"] == 18450  # after _on_response fed the budget
    assert attrs["donkey.correlation_id"] == "run-7f3a"  # equals the header actually sent


async def test_every_span_in_a_run_shares_the_run_id(monkeypatch) -> None:
    """Per-run id on EVERY span in the block (#195 AC): two model calls inside one
    ``donkey.run()`` open two spans, and both carry the same
    ``donkey.correlation_id`` — the run id, not a fresh id per call."""
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={_PROVIDER_HEADER: "openai"}, json=_SUCCESS_BODY)

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    with run_context("run-multi"):
        async with client:
            await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "a"})
            await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "b"})

    spans = exporter.get_finished_spans()
    assert len(spans) == 2  # one span per model call
    assert {dict(s.attributes)["donkey.correlation_id"] for s in spans} == {"run-multi"}


async def test_llm_refusal_records_refuse_decision_and_policy_type(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)  # empty body → TokenBudgetExceeded (docs/verified-apis.md §4)

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        resp = await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
    assert resp.status_code == 429

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["donkey.policy.decision"] == "refuse"
    assert attrs["donkey.policy.type"] == "token_budget"
    assert "gen_ai.usage.input_tokens" not in attrs  # no usage on a refusal


async def test_get_request_emits_no_span(monkeypatch) -> None:
    # A bodyless GET is not a GenAI call: no span, so the header-injection and
    # retry tests above remain byte-identical with telemetry on.
    exporter = _use_tracer(monkeypatch)
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    client = DonkeyAsyncClient(_LLM_CFG, None, transport=transport)
    async with client:
        await client.get("https://proxy/thing")
    assert exporter.get_finished_spans() == ()


async def test_post_without_model_emits_no_span(monkeypatch) -> None:
    # A POST that is not a model call (no `model` in the body) gets no span.
    exporter = _use_tracer(monkeypatch)
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    client = DonkeyAsyncClient(_LLM_CFG, None, transport=transport)
    async with client:
        await client.post("https://proxy/thing", json={"hello": "world"})
    assert exporter.get_finished_spans() == ()


async def test_span_suppressed_when_telemetry_disabled(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)
    cfg = DonkeyConfig(llm_proxy_url="https://proxy", telemetry=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_SUCCESS_BODY))
    client = DonkeyAsyncClient(cfg, None, transport=transport)
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
    assert exporter.get_finished_spans() == ()


async def test_streaming_response_span_omits_usage(monkeypatch) -> None:
    # A streaming (SSE) response carries no usage block on the envelope; usage
    # from the terminal event is deferred to #193. The span still opens and
    # records what it can (decision, provider).
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream; charset=utf-8",
                _PROVIDER_HEADER: "openai",
            },
            content=b"event: response.created\n\n",
        )

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o", "stream": True})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["donkey.policy.decision"] == "allow"
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs


async def test_transport_error_closes_span_without_masking(monkeypatch) -> None:
    # #179: a transport error escapes before _finish, so the span cannot rely on
    # _on_response — the context manager closes it in a finally. The underlying
    # error must still propagate unmasked.
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(GatewayUnavailable):  # typed wrapper; span lifecycle unchanged (#379)
            await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})

    (span,) = exporter.get_finished_spans()  # closed, not leaked
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"  # recorded at span start
    assert "donkey.policy.decision" not in attrs  # never reached _finish


def test_sync_llm_post_emits_one_span_with_both_namespaces(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={_PROVIDER_HEADER: "openai", "x-token-remaining": "9000"},
            json=_SUCCESS_BODY,
        )

    budget = Budget()
    client = DonkeyClient(_LLM_CFG, budget=budget, transport=httpx.MockTransport(handler))
    with client, run_context("run-sync"):
        resp = client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
    assert resp.status_code == 200

    (span,) = exporter.get_finished_spans()
    assert span.name == telemetry.SPAN_LLM_CHAT
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.usage.input_tokens"] == 1420
    assert attrs["donkey.policy.decision"] == "allow"
    assert attrs["donkey.budget.remaining"] == 9000
    assert attrs["donkey.correlation_id"] == "run-sync"


# --- refused requests and streaming span lifecycle (#193, BG §1.6) ----------
# #193 adds two guarantees on top of #192's contract:
#   1. a refusal closes a span with decision=refuse AND OTel status ERROR;
#   2. a streamed completion produces EXACTLY ONE span whose gen_ai.usage.* are
#      populated from the terminal SSE event, and the span is guaranteed to close
#      on full drain, mid-iteration abandonment, and exception.
# Streaming is the httpx transport-level `stream=True` (client.send(req,
# stream=True)) — distinct from a `"stream": true` field in the JSON body.

_PII_403 = {"error": {"type": "pii_detected", "message": '[{"pii_type": "EMAIL"}]'}}

# A Chat-Completions SSE stream that ends with a usage event (as emitted with
# stream_options={"include_usage": true}), then the [DONE] sentinel.
_SSE_WITH_USAGE = [
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    b'data: {"choices":[{"delta":{}}],'
    b'"usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14}}\n\n',
    b"data: [DONE]\n\n",
]

# A Responses-API-shaped terminal usage event carrying the #307 detail counts
# (cached / cache_write / reasoning), which arrive only in the final SSE event.
_SSE_WITH_USAGE_DETAILS = [
    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
    b'data: {"choices":[{"delta":{}}],'
    b'"usage":{"input_tokens":1420,"output_tokens":310,"total_tokens":1730,'
    b'"input_tokens_details":{"cached_tokens":512,"cache_write_tokens":128},'
    b'"output_tokens_details":{"reasoning_tokens":96}}}\n\n',
    b"data: [DONE]\n\n",
]


class _AsyncSSE(httpx.AsyncByteStream):
    """A minimal async byte stream so MockTransport can return a genuinely
    unread (streamed) SSE body — `content=` would buffer it instead."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _SyncSSE(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def _sse_response(chunks) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream", _PROVIDER_HEADER: "openai"},
        stream=chunks,
    )


def _span_status_code():
    # importorskip so the base-only job (no opentelemetry installed at all) SKIPS
    # these status-assertion tests instead of erroring on the bare import.
    pytest.importorskip("opentelemetry")
    from opentelemetry.trace import StatusCode

    return StatusCode


async def test_pii_refusal_span_has_refuse_decision_and_error_status(monkeypatch) -> None:
    # AC #1: a 403 pii_detected produces a span with decision=refuse AND OTel
    # status ERROR (not merely a refuse attribute — the span is a failed op).
    StatusCode = _span_status_code()
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=_PII_403)

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        resp = await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "e@x.io"})
    assert resp.status_code == 403

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["donkey.policy.decision"] == "refuse"
    assert attrs["donkey.policy.type"] == "pii_detected"
    assert span.status.status_code is StatusCode.ERROR


async def test_transport_error_span_has_error_status(monkeypatch) -> None:
    # AC #4: a transport error closes the span (context manager) AND leaves it in
    # the ERROR state — the failure is not silently a successful-looking span.
    StatusCode = _span_status_code()
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(GatewayUnavailable):  # typed wrapper; span still ERROR (#379)
            await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR


async def test_streaming_span_captures_usage_from_terminal_chunk(monkeypatch) -> None:
    # AC #2: a streamed completion produces EXACTLY ONE span, and gen_ai.usage.*
    # are populated from the terminal SSE usage event once the stream is drained.
    exporter = _use_tracer(monkeypatch)
    sse = _AsyncSSE(_SSE_WITH_USAGE)

    client = DonkeyAsyncClient(
        _LLM_CFG, None, transport=httpx.MockTransport(lambda r: _sse_response(sse))
    )
    async with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = await client.send(req, stream=True)
        # The span must NOT be finished mid-stream: usage isn't known yet.
        assert exporter.get_finished_spans() == ()
        lines = [line async for line in resp.aiter_lines()]

    assert any("[DONE]" in line for line in lines)
    assert sse.closed  # the underlying stream was closed, not leaked

    (span,) = exporter.get_finished_spans()  # exactly ONE span for the whole stream
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["donkey.policy.decision"] == "allow"
    assert attrs["gen_ai.usage.input_tokens"] == 11  # prompt_tokens from the usage event
    assert attrs["gen_ai.usage.output_tokens"] == 3  # completion_tokens from the usage event


async def test_streaming_usage_details_fill_span_and_last_call(monkeypatch) -> None:
    # #307 on the streaming path: the terminal SSE usage event carries the
    # cached / cache_write / reasoning detail counts. They land on the span's
    # donkey.usage.* attributes AND merge into donkey.last_call once the stream is
    # drained — both are unknown at send() time, since the body is unread then.
    exporter = _use_tracer(monkeypatch)
    sse = _AsyncSSE(_SSE_WITH_USAGE_DETAILS)

    client = DonkeyAsyncClient(
        _LLM_CFG, None, transport=httpx.MockTransport(lambda r: _sse_response(sse))
    )
    async with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = await client.send(req, stream=True)
        # Mid-stream: the record is already OBSERVED (a response arrived) but usage
        # is still None — the terminal event has not been read yet, and None is the
        # honest state, never a premature 0.
        mid = current_last_call()
        assert mid is not None and mid.status is LastCallStatus.OBSERVED
        assert mid.cached_tokens is None and mid.reasoning_tokens is None
        async for _line in resp.aiter_lines():
            pass

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.usage.input_tokens"] == 1420
    assert attrs["gen_ai.usage.output_tokens"] == 310
    assert attrs["donkey.usage.cached_tokens"] == 512
    assert attrs["donkey.usage.cache_write_tokens"] == 128
    assert attrs["donkey.usage.reasoning_tokens"] == 96

    # last_call now carries the merged usage from the terminal event.
    final = current_last_call()
    assert final is not None
    assert final.input_tokens == 1420
    assert final.output_tokens == 310
    assert final.total_tokens == 1730
    assert final.cached_tokens == 512
    assert final.cache_write_tokens == 128
    assert final.reasoning_tokens == 96


async def test_streaming_span_closes_when_abandoned_mid_iteration(monkeypatch) -> None:
    # AC #3: a stream abandoned after one chunk still closes its span (via the
    # caller's response.aclose(), which routes through the wrapping stream).
    exporter = _use_tracer(monkeypatch)
    sse = _AsyncSSE(_SSE_WITH_USAGE)

    client = DonkeyAsyncClient(
        _LLM_CFG, None, transport=httpx.MockTransport(lambda r: _sse_response(sse))
    )
    async with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = await client.send(req, stream=True)
        async for _chunk in resp.aiter_bytes():
            break  # consume one chunk, then walk away
        await resp.aclose()

    (span,) = exporter.get_finished_spans()  # closed, not leaked
    assert dict(span.attributes)["gen_ai.request.model"] == "gpt-4o"


async def test_streaming_span_closes_on_exception_during_iteration(monkeypatch) -> None:
    # AC #4: an exception raised mid-iteration still closes the span — a real
    # consumer (httpx's own stream ctx, the OpenAI SDK) closes in a finally.
    exporter = _use_tracer(monkeypatch)
    sse = _AsyncSSE(_SSE_WITH_USAGE)

    client = DonkeyAsyncClient(
        _LLM_CFG, None, transport=httpx.MockTransport(lambda r: _sse_response(sse))
    )
    async with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = await client.send(req, stream=True)
        with pytest.raises(RuntimeError):
            try:
                async for _chunk in resp.aiter_bytes():
                    raise RuntimeError("consumer blew up mid-stream")
            finally:
                await resp.aclose()

    (span,) = exporter.get_finished_spans()  # closed despite the exception
    assert dict(span.attributes)["gen_ai.request.model"] == "gpt-4o"


async def test_streaming_refusal_produces_one_span_with_error_status(monkeypatch) -> None:
    # A refused stream request (stream=True but the proxy returns a buffered 403)
    # is NOT an SSE body: the span closes immediately with decision=refuse and
    # status ERROR — exactly one span, no wrapper, nothing to drain.
    StatusCode = _span_status_code()
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=_PII_403)

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = await client.send(req, stream=True)
        # Refusal is buffered and terminal: the span is already closed.
        (span,) = exporter.get_finished_spans()
        await resp.aclose()
    attrs = dict(span.attributes)
    assert attrs["donkey.policy.decision"] == "refuse"
    assert attrs["donkey.policy.type"] == "pii_detected"
    assert span.status.status_code is StatusCode.ERROR
    assert len(exporter.get_finished_spans()) == 1  # still exactly one


def test_sync_streaming_span_captures_usage_from_terminal_chunk(monkeypatch) -> None:
    # The blocking twin: send(stream=True) + iter_lines() drains the SSE body and
    # the span carries usage from the terminal event, closed exactly once.
    exporter = _use_tracer(monkeypatch)
    sse = _SyncSSE(_SSE_WITH_USAGE)

    client = DonkeyClient(_LLM_CFG, transport=httpx.MockTransport(lambda r: _sse_response(sse)))
    with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = client.send(req, stream=True)
        assert exporter.get_finished_spans() == ()  # not finished mid-stream
        lines = list(resp.iter_lines())

    assert any("[DONE]" in line for line in lines)
    assert sse.closed

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 3


def test_sync_streaming_span_closes_when_abandoned_mid_iteration(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)
    sse = _SyncSSE(_SSE_WITH_USAGE)

    client = DonkeyClient(_LLM_CFG, transport=httpx.MockTransport(lambda r: _sse_response(sse)))
    with client:
        req = client.build_request(
            "POST", "https://proxy/chat", json={"model": "gpt-4o", "stream": True}
        )
        resp = client.send(req, stream=True)
        for _chunk in resp.iter_bytes():
            break
        resp.close()

    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes)["gen_ai.request.model"] == "gpt-4o"


# --- cost-attribution tags on the wire + in spans
# --- (docs/verified-apis.md §3, BG §1.7, #196) --------
# All header NAMES are UNVERIFIED placeholders (docs/verified-apis.md §3); these tests set the
# ``cost_*_header`` config overrides so the asserted header keys are deterministic
# and no placeholder warning is triggered. The span-attribute side carries the
# full value regardless of the header question (AC #4).

_COST_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy",
    correlation_header="x-correlation-id",
    call_id_header="x-call-id",
    cost_team_header="x-cost-team",
    cost_project_header="x-cost-project",
    cost_env_header="x-cost-env",
    cost_enduser_header="x-cost-enduser",
)


async def test_config_cost_tags_injected_as_request_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = _COST_CFG.with_overrides(
        cost=CostTags(team="support", project="triage-v2", env="prod", enduser_id="u-42")
    )
    async with DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(handler)
    ) as client:
        await client.get("https://proxy/thing")

    assert seen["x-cost-team"] == "support"
    assert seen["x-cost-project"] == "triage-v2"
    assert seen["x-cost-env"] == "prod"
    assert seen["x-cost-enduser"] == "u-42"


async def test_unset_cost_dimensions_emit_no_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support"))
    async with DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(handler)
    ) as client:
        await client.get("https://proxy/thing")

    assert seen["x-cost-team"] == "support"
    # An unset dimension is absent — not an empty header (that would be a config
    # mistake CostTags rejects; here it is simply "no tag").
    assert "x-cost-project" not in seen
    assert "x-cost-env" not in seen
    assert "x-cost-enduser" not in seen


async def test_run_scope_cost_override_wins_per_field_over_config() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support", env="prod"))
    async with DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(handler)
    ) as client:
        # A run overrides team + adds project; env falls back to the config tag.
        with run_scope("run-1", CostTags(team="triage-team", project="triage-v2")):
            await client.get("https://proxy/thing")

    assert seen["x-cost-team"] == "triage-team"  # run wins
    assert seen["x-cost-project"] == "triage-v2"  # run adds
    assert seen["x-cost-env"] == "prod"  # config falls through


def test_sync_client_injects_config_cost_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support"))
    with DonkeyClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.get("https://proxy/thing")

    assert seen["x-cost-team"] == "support"


def test_attribution_headers_snapshot_carries_config_cost() -> None:
    # The default_headers snapshot path (frameworks that take only a dict) carries
    # the set-once config tags; a later run-scope override can't reach a snapshot.
    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support", project="triage-v2"))
    headers = attribution_headers(cfg)
    assert headers["x-cost-team"] == "support"
    assert headers["x-cost-project"] == "triage-v2"


def test_cost_headers_use_placeholder_name_without_warning() -> None:
    # #522: the gateway-side cost header names are a VERIFIED-NEGATIVE result — the
    # deployed proxy has no inbound cost-tag ingestion, so the name is a
    # forward-looking convention, not an open unknown. With no config override the
    # header NAME is the placeholder AND the quiet default path emits NO
    # UnverifiedValueWarning (verified=True). Escalate the warning to an error so a
    # regression that re-arms it is caught here.
    from donkey_kit.core import _verify

    cfg = DonkeyConfig(cost=CostTags(team="support"))
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        headers = cost_headers(cfg, cfg.cost)
    assert headers[_verify.COST_TEAM_HEADER.placeholder] == "support"


def test_verified_inbound_attribution_headers_are_quiet() -> None:
    """#522: the six inbound correlation / cost / per-call-id request-header names
    are live-verified — ``X-Correlation-Id`` is read by the gateway, and the cost
    + per-call-id names are confirmed as a client-side / non-contract shape (the
    gateway ingests no such header). All six placeholders are ``verified=True``, so
    reading any of them emits NO ``UnverifiedValueWarning``. Pins the quiet path so
    a regression that re-arms the warning is caught in CI."""
    from donkey_kit.core import _verify

    quiet = (
        _verify.CORRELATION_ID_HEADER,
        _verify.CALL_ID_HEADER,
        _verify.COST_TEAM_HEADER,
        _verify.COST_PROJECT_HEADER,
        _verify.COST_ENV_HEADER,
        _verify.COST_ENDUSER_HEADER,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        for placeholder in quiet:
            assert placeholder.verified is True
            assert placeholder.get()  # reading it must not warn/raise


async def test_default_run_with_cost_tags_emits_no_unverified_warning() -> None:
    """#522 AC: a default call under ``donkey.run(team=…)`` — cost tags set, no
    header-name overrides, no application/business-group attribution — emits NONE of
    the six ``UnverifiedValueWarning``s that used to fire (``X-Correlation-Id`` /
    ``X-Donkey-Request-Id`` / ``X-Anypoint-Cost-*``). Unit-level mirror of the
    acceptance group-1 quiet-path assertion."""
    from donkey_kit.core import _verify

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    cfg = DonkeyConfig(cost=CostTags(team="support", project="triage", env="prod"))
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        async with _client(handler, cfg) as client:
            with run_context("run-522"):
                await client.get("https://x/thing")

    assert seen[CORRELATION_HEADER.lower()] == "run-522"
    assert seen[_verify.COST_TEAM_HEADER.placeholder.lower()] == "support"


def test_effective_cost_tags_merges_run_over_config() -> None:
    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support", env="prod"))
    assert effective_cost_tags(cfg) == cfg.cost  # no run scope: config as-is
    with run_scope("r", CostTags(team="triage")):
        merged = effective_cost_tags(cfg)
    assert merged == CostTags(team="triage", env="prod")


async def test_cost_tags_recorded_as_span_attributes(monkeypatch) -> None:
    # AC #4: tags land as donkey.cost.* span attributes carrying the full value,
    # independent of the (unverified) request-header name.
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={_PROVIDER_HEADER: "openai"}, json=_SUCCESS_BODY)

    cfg = _COST_CFG.with_overrides(
        cost=CostTags(team="support", project="triage-v2", env="prod", enduser_id="u-42")
    )
    client = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o"})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["donkey.cost.team"] == "support"
    assert attrs["donkey.cost.project"] == "triage-v2"
    assert attrs["donkey.cost.env"] == "prod"
    assert attrs["donkey.cost.enduser.id"] == "u-42"


async def test_run_scope_cost_override_reflected_in_span(monkeypatch) -> None:
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={_PROVIDER_HEADER: "openai"}, json=_SUCCESS_BODY)

    cfg = _COST_CFG.with_overrides(cost=CostTags(team="support"))
    client = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    async with client:
        with run_scope("run-1", CostTags(team="triage", project="triage-v2")):
            await client.post("https://proxy/chat", json={"model": "gpt-4o"})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["donkey.cost.team"] == "triage"  # run override
    assert attrs["donkey.cost.project"] == "triage-v2"  # run-added dimension


# --- Content redaction boundary threaded from config (#306, BG §1.6) --------
# Every governed call — the raw client (donkey.llm.client()) and every adapter —
# opens its span through this one transport, so a single default-config model
# call proves the boundary for BOTH routes: no message content on the span. The
# transport also forwards config.telemetry_capture_content to the span factory,
# where the opt-in gate lives (tested in test_telemetry).


async def test_default_model_call_emits_no_content_on_the_span(monkeypatch) -> None:
    # Default config: capture_content is False. A model call's span carries
    # metadata but never prompt/completion — the raw-client and adapter routes
    # both flow through here, so this covers both.
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={_PROVIDER_HEADER: "openai"}, json=_SUCCESS_BODY)

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        await client.post(
            "https://proxy/chat",
            json={"model": "gpt-4o", "input": "my SSN is 000-00-0000"},
        )

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"  # metadata still recorded
    assert "gen_ai.prompt" not in attrs
    assert "gen_ai.completion" not in attrs


def _spy_capture_content(monkeypatch) -> list[bool]:
    """Spy on the transport's span factories, capturing the ``capture_content``
    flag each buffered/streaming call is given. Yields inert handles so send()
    still completes. Returns the list the flags are appended to."""
    import contextlib

    seen: list[bool] = []

    @contextlib.contextmanager
    def fake_genai_span(*, enabled, capture_content=False):
        if enabled:
            seen.append(capture_content)
        yield telemetry.GenAiSpan(None)

    def fake_start_genai_span(*, enabled, capture_content=False):
        if enabled:
            seen.append(capture_content)
        return telemetry.GenAiSpan(None)

    monkeypatch.setattr("donkey_kit.core.transport.genai_span", fake_genai_span)
    monkeypatch.setattr("donkey_kit.core.transport.start_genai_span", fake_start_genai_span)
    return seen


@pytest.mark.parametrize("capture", [False, True])
async def test_async_send_forwards_capture_content_flag(monkeypatch, capture: bool) -> None:
    seen = _spy_capture_content(monkeypatch)
    cfg = DonkeyConfig(llm_proxy_url="https://proxy", telemetry_capture_content=capture)
    client = DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o"})  # buffered
        await client.post("https://proxy/chat", json={"model": "gpt-4o", "stream": True})
    assert seen == [capture, capture]  # buffered + streaming both forward the flag


@pytest.mark.parametrize("capture", [False, True])
def test_sync_send_forwards_capture_content_flag(monkeypatch, capture: bool) -> None:
    seen = _spy_capture_content(monkeypatch)
    cfg = DonkeyConfig(llm_proxy_url="https://proxy", telemetry_capture_content=capture)
    client = DonkeyClient(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    with client:
        client.post("https://proxy/chat", json={"model": "gpt-4o"})  # buffered
        client.post("https://proxy/chat", json={"model": "gpt-4o", "stream": True})
    assert seen == [capture, capture]


# --- gateway routing & fallback: never double-retry, opt-in raise (BG §1.1, #309) --
# The gateway's Enhanced Resilience routing is the FIRST recovery layer; an SDK
# retry stacked on a response it already failed over just multiplies latency
# against an outage the gateway is already handling. And a silent model
# substitution (served ≠ requested) is invisible unless the caller opts in.

_ROUTING_FALLBACK = "x-llm-proxy-routing-fallback"
_LLM_MODEL_HEADER = "x-llm-proxy-llm-model"
_ROUTING_TYPE_HEADER = "x-llm-proxy-routing-type"


async def test_does_not_retry_a_503_the_gateway_marked_a_fallback() -> None:
    # AC5: a retryable status carrying routing-fallback:true is terminal — the
    # gateway already failed over, so the SDK must not retry on top of it.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={_ROUTING_FALLBACK: "true"})

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 503
    assert calls["n"] == 1  # NOT retried — the gateway's failover is the recovery


async def test_still_retries_a_503_that_is_not_a_fallback() -> None:
    # The no-retry rule is fallback-specific: a plain transient 503 (no routing
    # header, or fallback:false) still retries to exhaustion as before.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # fallback:false is a definitive "no failover" → still retryable.
        return (
            httpx.Response(503, headers={_ROUTING_FALLBACK: "false"})
            if calls["n"] < 3
            else httpx.Response(200)
        )

    async with _client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = await client.get("https://x")
    assert resp.status_code == 200
    assert calls["n"] == 3  # fallback:false does not suppress the retry


def test_sync_does_not_retry_a_503_the_gateway_marked_a_fallback() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={_ROUTING_FALLBACK: "true"})

    with _sync_client(handler, DonkeyConfig(max_retries=3)) as client:
        resp = client.get("https://x")
    assert resp.status_code == 503
    assert calls["n"] == 1


def _substitution_handler(request: httpx.Request) -> httpx.Response:
    # 200, but the gateway served a different model than the body requested.
    return httpx.Response(
        200,
        headers={_LLM_MODEL_HEADER: "gpt-4o", _ROUTING_TYPE_HEADER: "ModelBased"},
        json={"model": "gpt-4o"},
    )


async def test_on_model_substitution_off_by_default_does_not_raise() -> None:
    # AC4: default config is "off" — a substitution is surfaced passively on
    # last_call, never as an exception. The call returns the served response.
    cfg = DonkeyConfig(llm_proxy_url="https://proxy")  # on_model_substitution defaults "off"
    async with _client(_substitution_handler, cfg) as client:
        resp = await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})
    assert resp.status_code == 200


async def test_on_model_substitution_raise_raises_typed_error() -> None:
    # AC4: opted in, a served ≠ requested model raises ModelSubstituted carrying
    # both models and the served provider — a hard determinism signal.
    from donkey_kit.core.errors import ModelSubstituted

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", on_model_substitution="raise")
    async with _client(_substitution_handler, cfg) as client:
        with pytest.raises(ModelSubstituted) as exc:
            await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})
    assert exc.value.requested_model == "gpt-5.1"
    assert exc.value.served_model == "gpt-4o"


async def test_on_model_substitution_raise_is_silent_when_models_match() -> None:
    # No substitution when the served model equals the requested one, even opted in.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={_LLM_MODEL_HEADER: "gpt-5.1"}, json={"model": "gpt-5.1"}
        )

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", on_model_substitution="raise")
    async with _client(handler, cfg) as client:
        resp = await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})
    assert resp.status_code == 200


async def test_on_model_substitution_raise_ignores_a_refusal() -> None:
    # A non-2xx is classified on its own terms — it is never a "silent
    # substitution", so the raise path leaves it alone even when opted in.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={_LLM_MODEL_HEADER: "gpt-4o"}, json=_PII_403)

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", on_model_substitution="raise")
    async with _client(handler, cfg) as client:
        resp = await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "e@x.io"})
    assert resp.status_code == 403  # returned, not raised as ModelSubstituted


def test_sync_on_model_substitution_raise_raises_typed_error() -> None:
    from donkey_kit.core.errors import ModelSubstituted

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", on_model_substitution="raise")
    with _sync_client(_substitution_handler, cfg) as client:
        with pytest.raises(ModelSubstituted) as exc:
            client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})
    assert exc.value.requested_model == "gpt-5.1"
    assert exc.value.served_model == "gpt-4o"


async def test_served_model_and_fallback_recorded_on_the_span(monkeypatch) -> None:
    # AC3: the served model, routing type and fallback flag land as span
    # attributes; a served ≠ requested model is the fastest read of a failover.
    exporter = _use_tracer(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                _PROVIDER_HEADER: "openai",
                _LLM_MODEL_HEADER: "gpt-4o",
                _ROUTING_TYPE_HEADER: "ModelBased",
                _ROUTING_FALLBACK: "true",
            },
            json=_SUCCESS_BODY,
        )

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-5.1"  # what the caller asked for
    assert attrs["gen_ai.response.model"] == "gpt-4o"  # what the gateway served
    assert attrs["donkey.routing.type"] == "ModelBased"
    assert attrs["donkey.routing.fallback"] is True


# --- GatewayUnavailable: transport-level failures are typed (#379, BG §1.2) --
# A transport error (DNS, refused, TLS, timeout) yields NO response, so it must
# surface as the typed GatewayUnavailable — not a raw httpx exception — and it
# must behave identically on the async and sync transports. A MockTransport
# handler that raises simulates the failure without a network.

# ConnectError is what httpx raises for DNS failure, connection refused AND TLS
# errors; the timeout family (a subclass of TransportError) covers timeouts.
_TRANSPORT_ERRORS = [
    httpx.ConnectError("nodename nor servname provided"),  # DNS failure
    httpx.ConnectError("[Errno 61] Connection refused"),  # connection refused
    httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"),  # TLS failure
    httpx.ConnectTimeout("timed out"),  # connect timeout
    httpx.ReadTimeout("timed out"),  # read timeout
]


def _raiser(exc: httpx.TransportError):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _assert_gateway_unavailable(err: GatewayUnavailable, cause: httpx.TransportError) -> None:
    assert err.base_url == "https://gw.example"  # the origin, not the full path
    assert err.cause is cause
    assert err.__cause__ is cause  # chained via `raise ... from`
    assert err.request_id is None  # no response → no gateway-minted id
    # Remediation names the three real causes and points at `donkey doctor`.
    assert "donkey doctor" in err.remediation
    assert "unreachable" in err.remediation
    assert "base URL" in err.remediation
    assert "egress" in err.remediation


@pytest.mark.parametrize("exc", _TRANSPORT_ERRORS, ids=lambda e: repr(str(e)))
async def test_transport_error_raises_gateway_unavailable_async(exc: httpx.TransportError) -> None:
    client = _client(_raiser(exc))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)  # placeholder header names
        async with client:
            with pytest.raises(GatewayUnavailable) as ei:
                await client.post("https://gw.example/v1/chat", json={"model": "m", "input": "hi"})
    _assert_gateway_unavailable(ei.value, exc)


@pytest.mark.parametrize("exc", _TRANSPORT_ERRORS, ids=lambda e: repr(str(e)))
def test_transport_error_raises_gateway_unavailable_sync(exc: httpx.TransportError) -> None:
    client = _sync_client(_raiser(exc))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)
        with client:
            with pytest.raises(GatewayUnavailable) as ei:
                client.post("https://gw.example/v1/chat", json={"model": "m", "input": "hi"})
    _assert_gateway_unavailable(ei.value, exc)


async def test_gateway_unavailable_carries_the_sent_ids() -> None:
    # The run correlation id and the per-call id the client stamped on the failed
    # request are carried, so an availability failure quotes the same ids a
    # response error would — even with no response (BG §1.1, #195).
    client = _client(_raiser(httpx.ConnectError("refused")))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)
        async with client:
            with run_context("run-boom"):
                with pytest.raises(GatewayUnavailable) as ei:
                    await client.post("https://gw.example/chat", json={"model": "m", "input": "x"})
    assert ei.value.correlation_id == "run-boom"
    assert ei.value.call_id is not None  # per-call id was pinned before the send


async def test_transport_error_is_not_retried() -> None:
    # A request that never got a response is not retried (retries key off a status
    # code) — the handler is invoked exactly once even with retries configured.
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    client = _client(handler, DonkeyConfig(max_retries=3))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)
        async with client:
            with pytest.raises(GatewayUnavailable):
                await client.post("https://gw.example/chat", json={"model": "m", "input": "x"})
    assert calls == 1


async def test_5xx_response_still_returns_a_response_not_gateway_unavailable() -> None:
    # Regression guard: a response-bearing failure (5xx) is NOT a transport error;
    # it flows through unchanged for classify() to type, never GatewayUnavailable.
    client = _client(lambda r: httpx.Response(503), DonkeyConfig(max_retries=0))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)
        async with client:
            resp = await client.post("https://gw.example/chat", json={"model": "m", "input": "x"})
    assert resp.status_code == 503
