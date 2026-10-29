# Connected-app scopes per operation

The Anypoint scopes a connected app needs for each SDK operation that calls
the control plane, and whether any of them needs an admin connected app acting
with user context (#23). The consumer version of this table, without the
verification columns, is on the docs site:
**<https://docs.donkey-kit.dev/reference/scopes>** (source:
[`website/content/reference/scopes.mdx`](../website/content/reference/scopes.mdx)).
The ledger rows are in [`verified-apis.md`](verified-apis.md) §1.

## How this was established

Every row comes from one live run in the sandbox business group, with
throwaway connected apps of type *App acts on its own behalf (client
credentials)*, all deleted afterwards:

- Each candidate scope set was granted to a **fresh** app. A scope removed from
  an app keeps working for a while, so re-scoping one app over-reports: an
  earlier pass of this run, which reused an app, wrongly found that
  `read:api_configuration` allows the policy read.
- The reads went through the SDK itself (`search()`, `resolve_mcp()`,
  `explain()` with `STRICT`) as well as direct requests. The writes were
  `donkey publish` runs and direct requests with valid bodies on throwaway
  assets and instances.
- Scope IDs are given, not the Access Management display names: the mapping
  from one to the other was not checked.

No operation needs an admin connected app or user context. Every row below
ran with `client_credentials` alone. MuleSoft documents that a connected app's
data access is bounded by the user who created it
([Creating Connected Apps](https://docs.mulesoft.com/access-management/creating-connected-apps-dev)),
so the creator must have access to the business group and environment too.

**Context** is where the scope is granted in Access Management: *org* means the
business group alone; *org + env* means the business group and the
environment the SDK targets (`ANYPOINT_ENV`). Exchange, API Governance and
`read:orgenvironments` scopes take no environment.

## Reads (`search()`, `resolve_mcp()`, `explain()`, `donkey.tools.discover()`)

| Operation | Call | Any one of these scopes | Context | Admin / user context | Without it | Date | Source |
|---|---|---|---|---|---|---|---|
| Connected-app token | `POST /accounts/api/v2/oauth2/token` | none: the app's `client_credentials` grant | — | No | — | 2026-10-28 | live run, sandbox, connected app (#23) |
| `search()` | `POST /graph/api/v1/graphql` | `read:exchange` | org | No | `200` with no assets: no error, `search()` returns `[]` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `resolve_mcp()`, `explain()`: environment id | `GET /accounts/api/organizations/{org}/environments` | `view:environment`, `read:orgenvironments`, `read:servers` | org + env (`read:orgenvironments`: org) | No | `401`, plain text `Unauthorized` → `AuthError` naming `view:environment` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `resolve_mcp()`, `explain()`: instance list | `GET /apimanager/api/v1/…/apis?assetId=&groupId=&assetVersion=` | `read:api_configuration`, `read:api_policies`, `manage:api_configuration`, `manage:apis` | org + env | No | `403` `{"name":"AuthorizationError","message":"Unauthorized"}` → `AuthError` naming `read:api_configuration` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `resolve_mcp()`: instance describe | `GET /apimanager/api/v1/…/apis/{id}` | `read:api_configuration`, `read:api_policies`, `manage:api_configuration`, `manage:apis` | org + env | No | as the instance list | 2026-10-28 | live run, sandbox, connected app (#23) |
| `explain()`: policies (`require_any_policy`, `required_policies`, `forbidden_policies`) | `GET /apimanager/api/v1/…/apis/{id}/policies` | `read:api_policies`, `manage:api_policies`. **Not** `read:api_configuration`. | org + env | No | `403` `AuthorizationError` → `AuthError` naming `read:api_policies` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `resolve_mcp()`: gateway public URL | `GET /gatewaymanager/api/v1/…/gateways/{id}` | `read:servers`. Not `manage:servers`, `read:api_configuration`, `manage:api_proxies` or `view:environment`. | org + env | No | `403` `{"status":403,"message":"Forbidden"}` → `AuthError` naming `read:servers` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `explain()`: tags and lifecycle (`require_tags`, `require_lifecycle`) | `GET /exchange/api/v2/assets/{group}/{asset}/{version}` | `read:exchange` | org | No | `403` `ForbiddenError` → the checks are UNKNOWN, their detail names `read:exchange` | 2026-10-28 | live run, sandbox, connected app (#23) |
| `explain()`: governance (`require_governance_pass`) | `GET /governance/xapi/api/v1/stats/organization/targets/…` | `read:exchange`, `view:ang_governance_profiles`, `admin:ang_governance_profiles` | org | No | `403`, text "The supplied authentication is not authorized to access this resource" → the check is UNKNOWN, its detail names `read:exchange` | 2026-10-28 | live run, sandbox, connected app (#23) |

So `read:exchange`, `read:servers` and `read:api_policies` are enough for every
read. The docs site recommends the five scopes the SDK's error messages name
(`view:environment`, `read:exchange`, `read:api_configuration`,
`read:api_policies`, `read:servers`), each one for the call it is named for;
that set was also run end to end.

## `donkey publish`

| Step | Call | Scope | Context | Admin / user context | Result with less | Date | Source |
|---|---|---|---|---|---|---|---|
| Publish a version (new or existing asset), register its endpoint, set name and description, write the page | the Exchange calls in [`verified-apis.md`](verified-apis.md) §12.3 | `manage:exchange` | org | No | `read:exchange`: `403` on the publish → `AuthError` naming `manage:exchange`. `create:exchange`: publishes a new asset's first version, then `404` "The requested instance does not exist." when registering its endpoint; `403` on an existing asset. `edit:exchange`: publishes a version and sets the asset's name and description (`PATCH` → `204`), then the same `404` when the version starts a new version group and so needs its endpoint registered → `RegistryError` naming `manage:exchange`. | 2026-10-28 | live run, sandbox, connected app (#23) |

## `donkey expose` and `donkey preflight` (#226, #1279)

`donkey expose` publishes the service with the `donkey publish` calls, then
makes the write path in [`verified-apis.md`](verified-apis.md) §12.5.
`donkey preflight` (and `donkey expose --dry-run`) make only its reads. A
fresh app holding exactly these seven scopes ran both commands end to end,
for an MCP server and an A2A agent, twice each:

| Scope | Context | Used for |
|---|---|---|
| `view:environment` | org + env | the environment id |
| `read:servers` | org + env | the gateway, by name, with its listener port, runtime version and public URL; the deploy needs it too |
| `read:exchange` | org | the published version, and the instance create, which reads the asset |
| `manage:exchange` | org | publishing the version, as `donkey publish` does |
| `manage:api_configuration` | org + env | the instance list, describe and upstreams, and the create |
| `manage:api_policies` | org + env | the policy list and the inbound and outbound policy writes |
| `manage:api_proxies` | org + env | the deploy and its status |

`donkey preflight` makes only the reads, so it needs `view:environment`,
`read:servers`, `read:exchange`, and the read scopes of the instance and policy
calls in the Reads table above. Each write, and what a smaller set answers:

| Call | Scope | Context | Admin / user context | Result with less | Date | Source |
|---|---|---|---|---|---|---|
| `POST /apimanager/api/v1/…/apis` (create an instance) | `manage:api_configuration` or `manage:apis`, **plus** `read:exchange` | org + env (`read:exchange`: org) | No | Either manage scope alone: `400` "Error while trying to retrieve asset. Reason: Forbidden". | 2026-10-28 | live run, sandbox, connected app (#23) |
| `POST /apimanager/api/v1/…/apis/{id}/policies` (inbound policy) | `manage:api_policies` | org + env | No | `read:api_policies` or `manage:api_configuration`: `403`. | 2026-10-28 | live run, sandbox, connected app (#23) |
| `POST /apimanager/xapi/v1/…/apis/{id}/policies/outbound-policies` | `manage:api_policies` | org + env | No | `manage:api_configuration`: `403`. | 2026-10-28 | live run, sandbox, connected app (#23) |
| `POST /proxies/xapi/v1/…/apis/{id}/deployments` (deploy) | `read:servers` **plus** `manage:api_proxies` | org + env | No | `manage:api_proxies`, `manage:api_configuration`, `manage:apis` or `manage:servers` alone, or `read:servers` with `manage:api_configuration`: `403`. | 2026-10-28 | live run, sandbox, connected app (#23) |
| `GET /proxies/xapi/v1/…/apis/{id}/deployments/{id}/status` | `manage:api_proxies` | org + env | No | not isolated: it ran with the seven scopes above. | 2026-10-28 | live run, sandbox, connected app (#226) |
| `DELETE /apimanager/api/v1/…/apis/{id}` | `manage:api_configuration` or `manage:apis` | org + env | No | none: `403`. `donkey expose` never deletes. | 2026-10-28 | live run, sandbox, connected app (#23) |

The policy writes were first checked up to authorization (#23): with
`manage:api_policies` they got past it to a `404` for an instance id that does
not exist. The `donkey expose` run (#226) then made both. The instance
describe (`GET …/apis/{id}`) and upstreams read also work with
`manage:api_configuration`, which that run had in place of
`read:api_configuration`.

## What a missing scope looks like

There is no single shape, which is why the SDK maps each call to the scope it
needs (`donkey_kit.registry._scopes`) rather than reading the response:

- Accounts answers `401` with the plain-text body `Unauthorized`.
- API Manager, Gateway Manager, Exchange and API Governance answer `403`, each
  with its own body (above).
- The Exchange Graph search answers `200` with no assets.

A `401` or `403` on a read that `resolve_mcp()` or `explain()` cannot do
without is an `AuthError` whose `remediation` names the scope and the business
group. A refused Exchange or API Governance read makes the `explain()` checks
that need it UNKNOWN, with the scope in their detail. A refused `donkey
publish` step is an `AuthError`, or for the endpoint registration a
`RegistryError`, naming `manage:exchange`. A refused `donkey expose` step
fails with the scope in its remediation, and the steps after it are skipped;
the instance create's `400` "retrieve asset" names `read:exchange`. The captures are in
`python/tests/fixtures/anypoint/missing_scope/`.
