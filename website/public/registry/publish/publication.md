# Publication & verify()

Status: Live. Shipped, and verified against a real governed gateway.

## The `Publication` object

The publication types are in `donkey_kit.experimental`, not `donkey_kit`, until
descriptor derivation (`preview()` and `export()`) is verified. They may change
in any release until then. [`verify()`](#verifying-at-runtime-verify) works
today; `preview()` and `export()` raise `NotImplementedError`.

```python
from donkey_kit.experimental import Contact, Publication, PublicationAssetType

pub = Publication(
    asset_type=PublicationAssetType.MCP_SERVER,  # MCP_SERVER | A2A_AGENT | AGENT | API
    group_id="${ANYPOINT_ORG_ID}",
    asset_id="hr-tools-mcp",
    version="1.3.0",                          # semver
    name="HR Tools",
    description="Employee lookup and leave-balance tools for HR agents.",

    # Discovery metadata
    tags=["hr", "internal", "agent-tool"],
    categories={"Domain": "People", "Lifecycle": "Production"},
    contact=Contact(team="People Platform", email="people-plat@acme.com"),

    # Type-specific descriptor — exactly one, matching asset_type
    descriptor="auto",                        # introspect the live server

    # Documentation pages, published alongside the asset
    docs=[
        ("home", "docs/exchange/overview.md"),
        ("getting-started", "docs/exchange/quickstart.md"),
    ],

    # Where it actually lives — metadata only
    endpoint="https://hr-tools.internal.acme.com/mcp",
)
```

As planned, `asset_type` will determine which descriptor is required and how it
is generated Roadmap:

| `asset_type` | Descriptor | Generated from |
|---|---|---|
| `MCP_SERVER` | MCP tool manifest — server info, tool names, descriptions, JSON Schema inputs | live `tools/list` against the running server |
| `A2A_AGENT` | A2A Agent Card | declared skills, endpoint, auth schemes, input/output modes |
| `AGENT` | agent descriptor (no A2A surface) | framework introspection, best-effort |
| `API` | OpenAPI / AsyncAPI | user-supplied file; no generation |

## `descriptor="auto"` — deriving the spec from code Roadmap

This section is the planned design: a `Publication` doesn't derive its
descriptor yet. Hand-maintained catalog descriptors go stale within a sprint, so generation is
the core of this feature.

Every supported framework already derives JSON Schema from function signatures,
type hints, and docstrings — `@tool` in LangChain and Strands, `FunctionTool` in
ADK and LlamaIndex, `@mcp.tool()` in the MCP Python SDK, and the equivalent
conventions in CrewAI, the OpenAI Agents SDK, and the Anthropic SDK. The SDK
**asks the framework for the schema it already computed** rather than
re-deriving it, so the catalog documents exactly the schema the model sees.

### Derivation modes

```python
descriptor="auto"          # object introspection — the default
descriptor="auto:live"     # live protocol introspection — highest fidelity
descriptor="auto:check"    # generate, diff against committed file, fail on mismatch
```

- **`auto:live`** starts the server, performs the MCP initialize handshake, and
  calls `tools/list` (plus `resources/list` and `prompts/list`). It is exactly
  what a client sees, but the server must actually run, with whatever
  credentials and network that needs.
- **`auto`** (the default) imports your module, locates the tool and agent
  objects, and reads their already-computed schemas. No server, no network,
  works in CI. Importing user code executes it, so tool definitions must be
  packages, in a planned table such as:

  ```toml
  [publication.entrypoints]
  "hr-tools-mcp" = "acme.hr.server:mcp"        # module:attribute
  "hr-agent"     = "acme.hr.agent:build_agent" # a zero-arg factory also works
  ```

- **`auto:check`** generates the descriptor, diffs it against the committed
  file, and fails on mismatch — useful as a CI gate.

There is no AST-only mode. Parsing source can name a tool but cannot produce
its schema without re-deriving it. Where importing your code is not acceptable,
`donkey scan` (above) is the static, no-execution view.

A planned `donkey publish --cross-check` will run `auto` and `auto:live` and diff
them. A disagreement will mean either dynamic registration the object graph does
not reflect, or a broken framework adapter.

  Type hints give the **shape**, not the **meaning** — `department: str` becomes
  `{"type": "string"}` and says nothing about which departments are valid, or
  when to use this tool over a similar one. `auto` will fail publication on a
  description that is tautological or missing, and `preview()` will report
  description quality. Today `donkey scan` already fails on such a description.

## Verifying at runtime: `verify()`

`Publication.verify(donkey)` is the read-only runtime check that a live MCP
server or A2A agent still matches what its version was published with. It writes nothing:
there is no runtime publish.

```python
from donkey_kit import Donkey
from donkey_kit.experimental import Publication, PublicationAssetType

pub = Publication(
    asset_type=PublicationAssetType.MCP_SERVER,
    group_id="${ANYPOINT_ORG_ID}",
    asset_id="hr-tools-mcp",
    version="1.3.0",
    name="HR Tools",
    description="Employee lookup and leave-balance tools for HR agents.",
)

async with Donkey.from_env() as donkey:
    drift = await pub.verify(donkey)                     # warns, returns the differences
    await pub.verify(donkey, raise_on_drift=True)        # raises PublicationDrift instead
```

It reads the `tools` of the version's `mcp-metadata` file from Exchange, and
lists the live server's tools through its gateway instance, with the same
governance check and credential as a [`donkey.tools`](https://docs.donkey-kit.dev/tool-access.md) binding:
an ungoverned server raises `UngovernedMcpServer` and is sent nothing. A tool
added or removed, a changed description, or a changed set of parameter names
is drift. Parameter types are not compared, because `donkey publish` records
only the plain annotations it reads without running your code.

For an `AGENT` (or `A2A_AGENT`) publication it reads the card the version was
published with (its `a2a-v1-card` file, or the `a2a-card` of a version published
before A2A 1.0) and the card the agent serves at
`.well-known/agent-card.json` through its gateway instance, with the same
governance check and gateway credential: an ungoverned agent raises
`RegistryError` and is sent nothing, and so does one whose gateway URL is not
[`https://`](https://docs.donkey-kit.dev/registry/discovery/mcp.md#resolving-an-mcp-server). A changed card description, a changed set
of default input or output modes, a skill added or removed, or a skill whose
description changed is drift. The fields that say where and how this deployment
serves the agent are not compared: `url`, `supportedInterfaces`,
`preferredTransport`, `protocolVersion`, `version` (your code's version, not the
asset's) and the security fields (`securitySchemes`, `security`,
`securityRequirements`), which the upstream declares and the gateway enforces.

You can read the live side yourself, with the same governance check and
credential and no cache: `live_tools()` returns what the MCP server's
`tools/list` returns now, and `live_card()` the card the A2A agent serves now.
Each takes the asset version as `group_id/asset_id/version`:

```python
tools = await donkey.tools.live_tools("${ANYPOINT_ORG_ID}/hr-tools-mcp/1.3.0")
card = await donkey.tools.live_card("${ANYPOINT_ORG_ID}/hr-agent/0.1.0")
```

Both methods are **provisional**: unlike the rest of `donkey.tools`, they may
change in a minor release, with a migration note in the release notes. See
[Stability](https://docs.donkey-kit.dev/reference/api.md#stability).

By default each difference is logged at `WARNING` and returned, so a drifted
catalog is loud without taking down production traffic. With
`raise_on_drift=True` it raises `PublicationDrift`, whose message
lists every difference. `API` publications are not verified and raise
`NotImplementedError`.

[`donkey verify`](https://docs.donkey-kit.dev/cli/ship.md#donkey-verify) runs the same check from CI, for every MCP
server and `a2a_v1` agent in the repository.

Both read the live side at a gateway URL that discovery read from the control
plane at `base_url`, so the gateway credential follows `base_url`'s rule. If
`base_url` comes from the working directory's `.donkey-kit.toml` or
`.donkey-kit.local.toml` and isn't a regional Anypoint host, the credential is
sent only when it comes from those files too, or when `DONKEY_TRUST_PROJECT_CONFIG`
is set. Otherwise `verify()` raises `ConfigError` before anything is sent, and
`donkey verify` reports each such asset as an `error` with that message and
exits `1`. See
[Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives).

## Next steps

- [Scan & publish](https://docs.donkey-kit.dev/registry/publish.md): how a publish is planned and run.
- [CI pipelines](https://docs.donkey-kit.dev/registry/publish/ci.md): run the publish from your pipeline.
