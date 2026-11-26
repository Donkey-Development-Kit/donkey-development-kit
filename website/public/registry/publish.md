# Scan & publish

Status: Live. Shipped, and verified against a real governed gateway.

`donkey scan`, `donkey publish` and `donkey verify` are live: publish plans
(`--dry-run` sends nothing) and registers each MCP server and agent in Exchange
from CI, and verify checks the live side against what was published. The
collision check and `descriptor="auto"` derivation are on the
[Roadmap](https://docs.donkey-kit.dev/roadmap.md); their sections are badged and show the planned design.

`donkey scan` walks your repository and finds every
[`@donkey.agent`](https://docs.donkey-kit.dev/decorators.md#donkeyagent) and [`@donkey.tool`](https://docs.donkey-kit.dev/decorators.md#donkeytool), plus
the MCP servers those tools name with `server=`. It produces one deterministic
model of them. Publishing registers that model with Exchange / Agent
Registry, and the [GitHub Action](https://docs.donkey-kit.dev/registry/publish/ci.md#from-github-actions) runs both on every merge
to `main`.

**The registry becomes a consequence of the code, not a chore.** A support agent
with six tools registered by hand has a stale tool list within a week; with the
Action, every merge updates it.

  **This complements MuleSoft's Agent Scanners.** Agent Scanners discover agents
  from Agentforce, Bedrock, Vertex AI, and Copilot Studio at **runtime**. Scan is
  the **design-time / CI** source for the agents you build in plain Python,
  which no cloud scanner can see. Its model feeds direct Exchange publishing
  today, and is planned to feed the platform's self-managed scanner too, so the
  platform stays the system of record.

## `donkey scan` — the model of your code Live

```bash
donkey scan .           # a summary, and any issues
donkey scan . --json    # the canonical model, for a CI diff
```

Scan is **static**: it parses your source and never imports or runs it. It
needs no credentials and writes no file. The model lists:

- **agents**: each `@donkey.agent`, with its name (a service ID), skills and
  description, and the **protocol** and **endpoint** you declare for it (see
  [Declaring how an agent is reached](#declaring-how-an-agent-is-reached)),
- **tools**: each `@donkey.tool`, with its parameters, description, `server`,
  `approval` and `risk`,
- **MCP servers**: each distinct `server=` ID and the tools that name it,
- **unattributed** tools that have no `server=`. Scan reports them and never
  guesses a server.
- for each MCP server, the **transport** and **endpoint** you declare for it
  (see below), or `null` when you declare none.

The model is deterministic: the same code always gives the same JSON, and its
`digest` leaves out file paths and line numbers, so moving code does not look
like a change. Scan also reports **issues** and exits `1` when it finds one:
a missing or tautological description, an invalid service ID, a non-literal
decorator argument it cannot read, or a service ID used twice. That way CI
fails before anything is published. See [`donkey scan`](https://docs.donkey-kit.dev/cli/ship.md#donkey-scan) for
the full rules.

### Declaring how an MCP server is reached

Publishing an MCP server needs to say how a client reaches it, and that is not
something scan can read from code: nothing ties a `server=` ID to the object
that serves it, and the URL a client uses depends on where the server is
deployed. So you declare it, one table per server ID, in `.donkey-kit.toml`
beside your code:

```toml
# .donkey-kit.toml (committed)
[donkey.mcp_servers.hr-tools]
transport = "streamable-http"
endpoint  = "https://hr-tools.acme.com/mcp"

[donkey.mcp_servers.sku-manager]
transport = "stdio"
```

- `transport` is one of the MCP transports: `stdio`, `streamable-http`, or
  `sse` (deprecated by the MCP specification, still accepted).
- `endpoint` is the `http` or `https` URL a client connects to, normally the
  gateway-fronted one. A `stdio` server is started as a process and has no
  endpoint. Don't put credentials in the URL. The endpoint is published as
  written, so scan refuses a URL with a user name or password, or a query
  parameter named like a credential (`token`, `secret`, `password`, `api_key`,
  `client_id`, `key`, `sig` and similar). The issue names the parameter, never
  its value.
- A `.donkey-kit.local.toml` beside it (gitignored) overrides the project file
  key by key, so each environment can point at its own endpoint without editing
  the committed file:

  ```toml
  # .donkey-kit.local.toml
  [donkey.mcp_servers.hr-tools]
  endpoint = "http://localhost:8080/mcp"
  ```

Scan reads only these two files in the scanned directory, not your user-wide
config file, and parses them without running anything. A server with no table
is reported with no transport and no endpoint. Scan never guesses one, and this
is not an issue, so `donkey scan` still exits `0`. An unknown transport, an
endpoint that is not an `http(s)` URL, an unknown key, or a table for an ID that
no `@donkey.tool` names is an issue and fails the scan.

The declaration is part of the scan `digest`, because it is part of what would
be published. Settings outside `[donkey.mcp_servers]` are unaffected:
`Donkey.from_env()` ignores the table.

### Declaring how an agent is reached

An agent's Exchange asset says which protocol it speaks and, for an A2A agent,
the URL its agent card names. Neither is in the decorator, so you declare them
the same way, one table per agent name:

```toml
# .donkey-kit.toml (committed)
[donkey.agents.support-triage]
protocol = "a2a_v1"
endpoint = "https://agents.acme.com/support-triage/"
```

- `protocol` is `a2a_v1`, for an agent served over A2A 1.0 such as
  [`donkey.serve()`](https://docs.donkey-kit.dev/registry/serve.md), or `other`. `a2a` (A2A 0.3) is refused with a hint,
  because `donkey.serve()` speaks 1.0.
- `endpoint` is the `http` or `https` URL the agent is served at, with the same
  rules as an MCP server's. An `a2a_v1` agent needs one.
- An agent with no table is published with protocol `other` and no endpoint.
  This is not an issue.
- `.donkey-kit.local.toml` overrides the file key by key, and the declaration is
  part of the digest, as for `[donkey.mcp_servers]`. A table for a name that no
  `@donkey.agent` uses is an issue.

  A service ID (`@donkey.agent(name=...)`, `@donkey.tool(server=...)`) names a
  service in the registry **permanently**, so choose it deliberately. Renaming
  it later would register a second service rather than rename the first.

## Planning a publish

```bash
donkey publish --dry-run .          # the assets a publish would register
donkey publish --dry-run . --json   # the same plan as canonical JSON (plan_format: 1)
```

The dry run scans your code the way `donkey scan` does, then prints one
Exchange asset per MCP server (type `mcp`) and per agent (type `agent`), with
its group ID, asset ID and version, and the digest a publish would compare
with the latest published version's. It makes **no network call** and needs no
credentials.

Exchange coordinates aren't in your code, so you declare them in a
`[donkey.publication]` table in `.donkey-kit.toml` beside your code:

```toml
# .donkey-kit.toml (committed)
[donkey.publication]
version  = "1.3.0"     # required, a semantic version
group_id = "acme"      # optional: defaults to your org_id (ANYPOINT_ORG_ID)
protocol_version = "2025-06-18"                                       # optional
provider = { organization = "Acme HR", url = "https://hr.acme.example" }  # optional

[donkey.publication.assets.hr-tools]   # a service ID; every key is optional
asset_id    = "hr-tools-mcp"           # default: the service ID
name        = "HR Tools"               # default: the service ID
description = "Look up employees and their managers."
version     = "1.4.0"                  # default: the table's version
documentation = "docs/hr-tools.md"     # Markdown that opens the asset's page
# protocol_version and provider can be set here too, overriding the table's
```

- An MCP server has no description in code, so give it one here. An agent
  defaults to its own description. Exchange and Agent Fabric show the name as
  the asset's title and the description under it. A name or description you
  change later is applied with the next new version, to every version.
- `protocol_version` is the MCP protocol revision your server speaks:
  `2024-11-05`, `2025-03-26`, `2025-06-18` or `2025-11-25`. `provider` is who
  provides it, with both `organization` and `url`. Agent Fabric shows them as
  the asset's protocol version and provider; without `provider` it shows
  "Other". Both describe an MCP server, so an agent's `assets` table can't set
  them; the table-wide ones apply to MCP servers only.
- `documentation` is a UTF-8 `.md` or `.markdown` file, relative to the scanned
  directory and inside it. Hidden path components and components whose name
  before the first dot is `credentials` or `secrets` (case-insensitive) are
  refused, including symlink targets. Configuration guides such as `config.md`,
  `docs/configuration.md` and `configuration/guide.md` are allowed. These naming
  checks do not sanitize the file's content; review it before publishing.
  The dry-run JSON shows the
  source path; review it before publishing. Its content opens the asset's page
  (see below).
- A `.donkey-kit.local.toml` beside it overrides the project file key by key,
  as for `[donkey.mcp_servers]`.
- The digest covers what would be published for that asset: its tools, their
  descriptions and parameters (an agent's skills), how the server or agent is
  reached, the asset's name and
  description, and the protocol version, provider and documentation content
  when you set them. Editing the documentation file is a change, so it needs a
  new version. The digest leaves out file paths, line numbers and the version, so
  moving code or bumping the version alone does not look like a change.

The dry run exits `1` on any issue, so CI can run it on every pull request:
every issue `donkey scan` reports, plus a missing group ID or version, a
version that isn't semantic, an unknown key, a protocol version or provider
the metadata can't carry (or one set for an agent), a documentation file that is missing or outside the
scanned directory, or an `assets` table for a service ID your code doesn't
define.

## Publishing MCP servers

```bash
donkey publish .            # publish what changed (--if-changed is the default)
donkey publish . --always   # publish even when the digest matches the latest version
donkey publish . --json     # each asset with its status and detail (publish_format: 1)
```

Without `--dry-run`, `donkey publish` registers each MCP server in the plan as
an Exchange asset of type `mcp`, in the group and at the version you declared.
It runs from CI with the control-plane credentials: `ANYPOINT_CLIENT_ID`,
`ANYPOINT_CLIENT_SECRET` and `ANYPOINT_ORG_ID`. The connected app needs
the `manage:exchange` scope on that business group: with `edit:exchange` or
`create:exchange` only, a version can be published but its endpoint cannot be
registered. See [Connected-app scopes](https://docs.donkey-kit.dev/reference/scopes.md). Publishing has been checked against a live sandbox
with a connected app's token.

Each asset is published with:

- its name and description. Exchange keeps the ones an asset's first version
  was published with, so when a later version declares a different name or
  description, `donkey publish` sets it on the asset after publishing the
  version. A description you don't declare leaves the asset's as it is.
- the digest, stored with the asset so the next run can compare it,
- an `mcp-metadata` file: the transport, the tools with their descriptions and
  an input schema from their parameters, the capabilities (tools, and no
  resources, prompts or logging, since scan sees only tools), the protocol
  version and provider you declared, and the asset's coordinates. A
  parameter annotated `str`, `int`, `float`, `bool`, `list` or `dict` gets that
  JSON Schema type; any other parameter accepts any value. No parameter is
  marked required, because scan does not record defaults.
- the endpoint you declared in `[donkey.mcp_servers]`, if any, registered as an
  external instance. Exchange keeps instances per minor version, so a new patch
  version reuses the existing one.
- a Home page, generated in Markdown. Agent Fabric shows it as the asset's
  Documentation section. Every page has the same sections: an Overview with
  the description (or "No description is declared.") followed by your
  `documentation` file; the tools, each with its description, the `approval`
  and `risk` it declares, and a table of its parameters; on a new version of
  an existing asset, "Changes since" the latest published version (tools
  added or removed, a description changed, a parameter added, removed or given
  another type), which a first publish leaves out; how to connect
  (transport, endpoint, protocol version); and provenance: the asset ID and version, the content digest and, when the
  publish runs in GitHub Actions, the repository, the commit and a link to the
  run. It never includes credentials or your org ID. Only a new version gets a
  page; an unchanged asset keeps the one it has. A rerun that
  [finishes a publish](#when-a-publish-fails) writes the page the first run
  would have, with "Changes since" the published version before it.

A publish is safe to repeat:

- With `--if-changed`, an asset whose latest published version has the same
  digest is left alone. A merge that changes nothing, or that only bumps the
  version, publishes nothing.
- Exchange never overwrites a version. If you change an MCP server without
  bumping its version, `donkey publish` stops before sending and tells you to
  bump it. The same happens if the version is already published with other
  content.
- A rerun finishes a publish that stopped partway. See below.

### When a publish fails

Assets are published one at a time, in plan order. When Exchange refuses a
call, `donkey publish` stops. The assets published before it stay published,
and `--json` still prints every asset with its `status` and `detail`:

| `status` | Meaning |
|---|---|
| `published` | A new version was published, or a rerun finished an `incomplete` one. |
| `unchanged` | The latest version already has this digest, and its publish is complete. |
| `incomplete` | The version is published, but registering its endpoint, writing its page, or setting the asset's name and description was refused. |
| `failed` | Exchange refused the asset, and nothing was published for it. |
| `not_attempted` | An earlier asset failed, or an issue stopped the publish, so this one was not tried. |

When the scan or the declaration has an issue, nothing is sent: `--json`
prints every asset `not_attempted`, with the issues, and the command exits
`1`. Each `--json` document carries a format key; see
[JSON output](https://docs.donkey-kit.dev/cli.md#json-output).

The refusal and its fix go to stderr, and a refused asset's `detail` ends with
the same fix. A common one: a connected app without `manage:exchange` can
publish a version but not register its endpoint, so the asset is `incomplete`.

Fix what was reported and run `donkey publish` again, with the same version.
A version that has the planned digest counts as `unchanged` only once its
publish is complete. If it is not, the rerun does whatever is missing:

- registers the endpoint, when the version's instances don't list it,
- writes and publishes the Home page, when the version has no published one
  (for an MCP server, it first reads the tools of the published version
  before it, for the page's "Changes since"),
- sets the declared name and description, when the asset's differ,

and reports the asset `published`, with a detail such as `finished publishing
1.0.0: registered its endpoint`. Nothing is published twice, and a complete
version gets no write. Checking the page reads the version's portal draft, one
more read per unchanged asset.

`donkey publish` exits `0` when every asset is `published` or `unchanged`. It
exits `1` when the plan has an issue (nothing is sent) or an asset is `failed`
or `incomplete`, and `2` when the path doesn't exist, the config is invalid,
or the control-plane credentials are missing.

## Publishing agents

The same `donkey publish` registers each agent as an Exchange asset of type
`agent`, with the same credentials, the same digest check and the same
version-bump rule. Each agent asset is published with:

- its name and description, as for an MCP server. An agent's description
  defaults to its docstring.
- the digest, and the protocol you declared (`other` when you declared none),
  stored with the asset. Exchange stores an `a2a_v1` agent's protocol as `a2a`,
  with the version carried by its A2A 1.0 card, so the agent is listed as
  protocol `a2a`, version `v1`. That is the form the gateway accepts when
  [`donkey expose`](https://docs.donkey-kit.dev/registry/serve.md) fronts the agent.
- an `agent-metadata` file: the protocol, the agent's name and description,
  and its asset coordinates.
- for an `a2a_v1` agent, an **A2A agent card** (`a2a-v1-card`): the name,
  description, version, skills, input and output modes and the endpoint, as an
  A2A 1.0 card. It is built from the same `@donkey.agent` record as the card
  [`donkey.serve()`](https://docs.donkey-kit.dev/registry/serve.md) serves, so the two match.
- the endpoint, if you declared one, registered as an external instance.
- a Home page with the same Overview and provenance as an MCP server's, the
  agent's skills, and how to connect (protocol and endpoint).

Publishing agents has been checked against a live sandbox, as for MCP servers.

## Scope: code-first assets

Publication is for assets that **originate in your code**:

- an MCP server written in Python or TypeScript,
- an agent exposed over [A2A](https://docs.donkey-kit.dev/registry/serve.md),
- an agent exposed as a tool without an A2A surface.

It is not for assets the platform already owns. An MCP server created by MCP
Bridge from an existing API is already in Exchange; publishing a second,
code-derived descriptor would create two catalog entries for one capability.

### Collision check Roadmap

`donkey publish` doesn't run this check yet. As planned, before publishing the
SDK will search Exchange for an existing asset with the same endpoint, name, or
derived tool signature. On a probable match it will **refuse** and print the
existing asset's coordinates, and a planned `--allow-duplicate` flag will
override it with a `WARNING`.

## Related

- [CI pipelines](https://docs.donkey-kit.dev/registry/publish/ci.md) — the GitHub Actions, GitLab CI and Bitbucket Pipelines recipes.
- [Publication & verify()](https://docs.donkey-kit.dev/registry/publish/publication.md) — the Publication object, descriptor derivation and runtime verification.
- [Decorators](https://docs.donkey-kit.dev/decorators.md) — mark tools with `@donkey.tool` today.
- [A2A agents](https://docs.donkey-kit.dev/registry/serve.md) — serve and expose the agent whose card you publish.
- [Tool access](https://docs.donkey-kit.dev/tool-access.md) — discover and bind published tools from Exchange.
