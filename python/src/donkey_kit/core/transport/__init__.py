"""Transport — the single place headers get injected (BG §1.1).

This is the most important piece of engineering in the SDK. Every framework has
a different mechanism for setting request headers, and several have none. The
solution is one shared HTTP client per credential plane: every adapter is handed
the data-plane (LLM proxy) client, and Anypoint platform calls use a separate
control-plane client, so neither credential ever rides the other's requests.

The client:
  * injects, via a request event hook, on every outbound request:
      - the run correlation ID (uuid4 per logical agent run, from a contextvar,
        BG §1.6) — shared by every request in a ``donkey.run()`` block, the
        client↔gateway join key
      - attribution headers (application, business group) — header NAMES are
        UNVERIFIED (docs/verified-apis.md §3), emitted via loud placeholders
      - bearer token, refreshed lazily
  * pins a per-call ID ONCE, before the retry loop, so it is unique per logical
    request yet stable across that request's retries and 401 refresh (BG §1.1,
    #195). Two ids, two headers: the run id (``X-Correlation-Id``) groups a run;
    the call id (``X-Donkey-Request-Id``) pinpoints one request within it. Both
    header NAMES are UNVERIFIED placeholders (docs/verified-apis.md §3), overridable via config.
  * retries transient upstream/gateway failures (502/503/504) with exponential
    backoff + jitter, honouring Retry-After. A 502/504 on a model ``POST`` is
    not re-sent unless ``retry_model_calls_on_gateway_errors`` is on: the
    upstream call may have completed and billed (docs/adr/0009-*.md)
  * does NOT retry 4xx — gateway policy rejections are terminal (BG §1.2). This
    includes 429: on this proxy a 429 is a token-budget refusal
    (TokenBudgetExceeded), and retrying it only burns the same exhausted window
    (BG §1.2, #183). retry_after is still surfaced for wait_for_reset() (#186).
    A final 4xx is stamped ``x-should-retry: false``, so the openai and
    anthropic SDKs above the transport do not retry it either (#734).
  * refreshes the attached provider's token and retries exactly once on 401
    (BG §1.1); a client-id data-plane client has no provider, so its 401 is
    terminal
  * keeps one connection pool per event loop, so a sync app that wraps each
    call in ``asyncio.run()`` can reuse one client (#807)

For frameworks that only accept a ``default_headers`` dict (not a client), pass
:func:`attribution_headers` — a snapshot — and accept that the correlation ID is
per-client rather than per-run. Document that degradation per adapter (BG §1.8).

The package splits that pipeline by concern (#728), with one copy for both
clients: :mod:`.headers` (what is stamped where), :mod:`.policy` (the sans-IO
retry, refusal and substitution decisions), :mod:`.observe` (budget,
``last_call``, span attributes, log records), :mod:`.streaming` (the span-closing
stream wrappers), :mod:`.pipeline` (the mixin both clients share),
:mod:`.governed` (the swappable :class:`GovernedTransport` and the pools under
it), :mod:`.failures` (the typed errors a failed send raises),
:mod:`.async_client` / :mod:`.sync_client` / :mod:`.views`, and
:mod:`.httpx2`, the bridge for frameworks built on ``httpx2``. That last module
imports ``httpx2``, which is not a base dependency, so it is never imported
here. Every name below keeps its ``donkey_kit.core.transport`` import path.
"""

from __future__ import annotations

from .async_client import DonkeyAsyncClient, build_http_client
from .failures import sync_token_auth_error
from .governed import GovernedSyncTransport, GovernedTransport
from .headers import (
    CALL_ID_HEADER,
    CORRELATION_HEADER,
    CREDENTIAL_HEADERS,
    PROXY_API_KEY_SENTINEL,
    Origin,
    attribution_headers,
    cost_headers,
    effective_cost_tags,
    origin_of,
    proxy_api_key,
    proxy_auth_headers,
    strip_credential_headers,
)
from .sync_client import DonkeyClient, build_sync_http_client
from .views import DonkeyAsyncClientView, DonkeyClientView

__all__ = [
    "CALL_ID_HEADER",
    "CORRELATION_HEADER",
    "CREDENTIAL_HEADERS",
    "PROXY_API_KEY_SENTINEL",
    "DonkeyAsyncClient",
    "DonkeyAsyncClientView",
    "DonkeyClient",
    "DonkeyClientView",
    "GovernedSyncTransport",
    "GovernedTransport",
    "Origin",
    "attribution_headers",
    "build_http_client",
    "build_sync_http_client",
    "cost_headers",
    "effective_cost_tags",
    "origin_of",
    "proxy_api_key",
    "proxy_auth_headers",
    "strip_credential_headers",
    "sync_token_auth_error",
]
