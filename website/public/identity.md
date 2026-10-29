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

Every governed model call in the block carries the user's token, unchanged, so
the gateway's Trusted Agent Identity layer can apply per-user policy. The SDK
runs no token exchange of its own: the gateway's on-behalf-of policy trades the
user token at your identity provider. So `as_user()` makes no Anypoint
platform request and needs no connected-app credentials.

- **Scoped to the block.** The user is bound to a context variable, like
  `donkey.run(...)`. It reaches the `asyncio` tasks the block starts and never
  leaks into a concurrent task that entered no block. A nested block restores
  the outer user when it exits.
- **Forwarded as is.** The SDK doesn't read the token's claims or expiry. An
  expired token comes back from the gateway as a typed error.
- **Model calls only.** The user token goes on requests to the LLM proxy. Calls
  to the Anypoint platform keep the service's own credentials, and a request to
  any other origin never carries it.

## Configuring the header

  The header the gateway reads the user token from is **not confirmed**
  against a live gateway yet. It is a placeholder that warns once when used,
  and you can override it:

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_USER_TOKEN_HEADER` | `user_token_header` | `Authorization` | The request header the user token rides in. `Authorization` carries it as `Bearer <token>`; any other header carries the raw token. |

`token_exchange_path` (`DONKEY_TOKEN_EXCHANGE_PATH`) is deprecated and ignored:
setting it makes `as_user()` emit a `DeprecationWarning`.

In the `jwt` and `bearer` [auth modes](https://docs.donkey-kit.dev/reference/configuration.md#bearer-token-auth-mode)
the data-plane token already rides in `Authorization`, so `as_user()` raises a
`ConfigError` until you set `user_token_header` to the header your gateway
reads the user token from.

## The problem this solves

An HR bot must not answer a manager's salary question about a *different*
manager's report.

That decision belongs to the gateway — it is the component that holds the
identity policy, the org-chart relationships, and the audit obligation. DDK's
job is to get the user's token onto the request correctly so the gateway can
make the call it is already able to make.

## Where the boundary sits

  **DDK does not implement authorisation logic.** Trusted Agent Identity is a
  MuleSoft gateway feature. DDK forwards the user token and places the
  header — nothing more. "Check the user's role in the SDK" is client-side
  enforcement, and it is on the [will-not-build list](https://docs.donkey-kit.dev/roadmap.md): code in your
  process can be bypassed by code in your process.

## No silent fallback: a deliberate hard failure

The failure mode to design against is a lost user token that lets the call
quietly proceed under the **service** identity. Per-user policy would stop
applying while everything still appears to work.

So inside `as_user()` a model call carries the user's token or it is not sent.
A missing user token is a **deliberate hard failure**: it raises `AuthError`,
with a `.remediation`, before any request leaves the process.

| What went wrong | When it raises | The error names |
|---|---|---|
| No `id_token`: empty, blank or `None` | `donkey.as_user(...)` itself | the missing `id_token` |

The errors and the SDK's logs never contain the token itself. There is **no
setting that enables a fallback** to the service identity, and none will be
added: a block that can't run as the user doesn't run.

Adapters that only take `connection_kwargs()` and don't route through the shared
transport never see the block, so their calls are not made as the user.
