# Identity

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

By default the gateway sees your **service**, not the person the agent is
acting for. That is fine until policy depends on who is asking.
`donkey.as_user()` runs a block of agent code on behalf of a user:

```python
from donkey_kit import Donkey

async def answer(donkey: Donkey, id_token: str, question: str) -> None:
    openai = donkey.llm.client()
    async with donkey.as_user(id_token=id_token):
        # The gateway sees the user, not just the service.
        await openai.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": question}]
        )
```

Entering the block trades the user's OIDC `id_token` for a gateway-scoped access
token with an [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) token exchange.
Every governed model call in the block then carries that token, so the
gateway's Trusted Agent Identity layer can apply per-user policy.

- **Scoped to the block.** The user is bound to a context variable, like
  `donkey.run(...)`. It reaches the `asyncio` tasks the block starts and never
  leaks into a concurrent task that entered no block. A nested block restores
  the outer user when it exits.
- **Cached to expiry.** Each user's exchanged token is reused until 60 seconds
  before its `expires_in`. A response without `expires_in` is not cached, so the
  next call exchanges again.
- **Model calls only.** The user token goes on requests to the LLM proxy. Calls
  to the Anypoint platform keep the service's own credentials, and a request to
  any other origin never carries it.

## Configuring the exchange

The exchange is a form `POST` to the control-plane host, authenticated with the
connected app (`ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET`), which
`as_user()` therefore requires.

  The token-exchange endpoint path and the header the gateway reads the user
  token from are **not confirmed** against a live gateway yet. Both are
  placeholders that warn once when used, and you can override each one:

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_TOKEN_EXCHANGE_PATH` | `token_exchange_path` | `/accounts/api/v2/oauth2/token` | The path on the control-plane host that serves the RFC 8693 grant. Only the path can change, so the user's token only goes to the control-plane host. |
| `DONKEY_USER_TOKEN_HEADER` | `user_token_header` | `Authorization` | The request header the exchanged token rides in. `Authorization` carries it as `Bearer <token>`; any other header carries the raw token. |

In the `jwt` and `bearer` [auth modes](https://docs.donkey-kit.dev/reference/configuration.md#bearer-token-auth-mode)
the data-plane token already rides in `Authorization`, so `as_user()` raises a
`ConfigError` until you set `user_token_header` to the header your gateway
reads the user token from.

The blocking client stamps the token obtained when the block was entered,
because it can't run an exchange itself. Once that token expires, a blocking
call raises `AuthError` instead of sending without it (see
[No silent fallback](#no-silent-fallback-a-deliberate-hard-failure)).

## The problem this solves

An HR bot must not answer a manager's salary question about a *different*
manager's report.

That decision belongs to the gateway — it is the component that holds the
identity policy, the org-chart relationships, and the audit obligation. DDK's
job is to get the user's token onto the request correctly so the gateway can
make the call it is already able to make.

## Where the boundary sits

  **DDK does not implement authorisation logic.** Trusted Agent Identity is a
  MuleSoft gateway feature. DDK does the token-exchange plumbing and header
  placement — nothing more. "Check the user's role in the SDK" is client-side
  enforcement, and it is on the [will-not-build list](https://docs.donkey-kit.dev/roadmap.md): code in your
  process can be bypassed by code in your process.

## No silent fallback: a deliberate hard failure

The failure mode to design against is a token exchange that quietly fails and
lets the call proceed under the **service** identity. Per-user policy would stop
applying while everything still appears to work.

So inside `as_user()` a model call carries the user's token or it is not sent.
A missing or expired user token is a **deliberate hard failure**: it raises
`AuthError`, with a `.remediation`, before any request leaves the process.

| What went wrong | When it raises | The error names |
|---|---|---|
| No `id_token`: empty, blank or `None` | `donkey.as_user(...)` itself | the missing `id_token` |
| The exchange refuses the `id_token` (expired, revoked, not valid for the connected app) or returns a malformed response | entering the block | the `id_token`, the status and the exchange path |
| The exchanged token expires and the re-exchange is refused | the next async model call | the `id_token` and the status |
| The exchanged token expires on a blocking client | the next blocking model call | the expired exchanged token |

The errors and the SDK's logs never contain the token itself. There is **no
setting that enables a fallback** to the service identity, and none will be
added: a block that can't run as the user doesn't run.

Adapters that only take `connection_kwargs()` and don't route through the shared
transport never see the block, so their calls are not made as the user.
