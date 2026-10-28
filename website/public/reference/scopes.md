# Connected-app scopes

Status: Live. Shipped, and verified against a real governed gateway.

Which scopes does the connected app behind `ANYPOINT_CLIENT_ID` /
`ANYPOINT_CLIENT_SECRET` need? This page lists them per operation. Model calls
need none of them: they use the LLM proxy's own credentials (see
[Anypoint control plane](https://docs.donkey-kit.dev/reference/configuration.md#anypoint-control-plane)).

Create the app as **App acts on its own behalf (client credentials)** in
Access Management. No operation needs an app that acts on behalf of a user, or
admin rights: the scopes below are enough. The app can see only what the user
who created it can see, so that user needs access to the business group and
environment too.

## Recommended grant

| Scope | Grant on | Used by |
|---|---|---|
| `read:exchange` | the business group | `search()`, `explain()` tag, lifecycle and governance checks |
| `view:environment` | the business group and the `ANYPOINT_ENV` environment | `resolve_mcp()`, `explain()` |
| `read:api_configuration` | the business group and the environment | `resolve_mcp()`, `explain()` |
| `read:api_policies` | the business group and the environment | `explain()` policy checks |
| `read:servers` | the business group and the environment | `resolve_mcp()` |
| `manage:exchange` | the business group | `donkey publish` |

Leave out `manage:exchange` if the app never publishes. The SDK's error
messages name the scopes in this table. A smaller set also works for the
reads: `read:exchange`, `read:servers` and `read:api_policies`.

### For `donkey expose`

`donkey expose` publishes the service, creates its gateway instance, applies
its policies and deploys it. An app with these seven scopes runs it, and
`donkey preflight`, end to end:

| Scope | Grant on | Used for |
|---|---|---|
| `view:environment` | the business group and the environment | finding the environment |
| `read:servers` | the business group and the environment | finding the gateway; the deploy needs it too |
| `read:exchange` | the business group | the published version, and the instance create |
| `manage:exchange` | the business group | publishing the version |
| `manage:api_configuration` | the business group and the environment | reading and creating the instance |
| `manage:api_policies` | the business group and the environment | reading and applying the policies |
| `manage:api_proxies` | the business group and the environment | the deploy |

`donkey preflight` only reads, so the read scopes in the table above are enough
for it.

  Removing a scope from an app does not take effect at once: the app keeps it
  for a while. To check that an app works with fewer scopes, create a new one
  with just those.

## Per operation

Each row lists the scopes that allow the call; any one of them is enough
unless the row says **plus**.

| Operation | Platform call | Scopes | Status |
|---|---|---|---|
| Connected-app token | `POST /accounts/api/v2/oauth2/token` | None | Live |
| `donkey.registry.search()` | Exchange Graph search | `read:exchange` | Live |
| `resolve_mcp()`, `explain()`: find the environment | Access Management environment list | `view:environment`, `read:orgenvironments` or `read:servers` | Live |
| `resolve_mcp()`, `explain()`: find the instance | API Manager instance list and describe | `read:api_configuration`, `read:api_policies`, `manage:api_configuration` or `manage:apis` | Live |
| `explain()`: `require_any_policy`, `required_policies`, `forbidden_policies` | API Manager instance policies | `read:api_policies` or `manage:api_policies`. `read:api_configuration` is not enough. | Live |
| `resolve_mcp()`: the gateway's public URL | Omni Gateway Manager gateway read | `read:servers` | Live |
| `explain()`: `require_tags`, `require_lifecycle` | Exchange asset version read | `read:exchange` | Live |
| `explain()`: `require_governance_pass` | API Governance conformance read | `read:exchange`, `view:ang_governance_profiles` or `admin:ang_governance_profiles` | Live |
| `donkey.tools.discover()` | The `search()`, `explain()` and `resolve_mcp()` reads | Theirs | Live |
| `donkey publish` | Exchange publish, endpoint registration, asset name and description, page | `manage:exchange`. `create:exchange` and `edit:exchange` are not enough. | Live |
| `donkey preflight`, `donkey expose`: find the gateway | Omni Gateway Manager gateway list and read | `read:servers` | Live |
| `donkey expose`: create an instance | API Manager instance create | `manage:api_configuration` or `manage:apis`, **plus** `read:exchange` | Live |
| `donkey expose`: inbound and outbound policies | API Manager policies | `manage:api_policies` | Live |
| `donkey expose`: deploy, and its status | Proxy deployment | `read:servers` **plus** `manage:api_proxies` | Live |

Exchange, API Governance and `read:orgenvironments` scopes are granted on the
business group only. The others are granted on the business group and the
environment.

## When a scope is missing

| Where | What you get |
|---|---|
| `resolve_mcp()`, `explain()` | `AuthError`. Its `remediation` names the scope the call needs and the business group, for example "The connected app likely lacks the scope 'read:servers' on business group …". |
| `explain()` Exchange and API Governance checks | No error: the check is UNKNOWN (`passed=None`), and its detail names `read:exchange`. |
| `search()` | No error: Exchange returns no assets, so `search()` returns `[]`. If a search you expect to match returns nothing, check `read:exchange` first. |
| `donkey publish` | `AuthError` naming `manage:exchange`. If the app has `edit:exchange` instead, the version can be published but registering its endpoint fails with a `RegistryError` naming `manage:exchange`. |
| `donkey expose`, `donkey preflight` | The step fails (`✗`), its remediation names the scope, and the steps after it are skipped. An instance create without `read:exchange` answers `400` "Error while trying to retrieve asset", which the step reports as a missing `read:exchange`. |

See [Errors](https://docs.donkey-kit.dev/errors.md) for how `AuthError` fits the error taxonomy.
