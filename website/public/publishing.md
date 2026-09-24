# Scan & publish

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

`donkey scan` walks your repository, finds everything marked
[`@donkey.tool`](https://donkey-development-kit.github.io/donkey-development-kit/cli.md#donkeytool), plus MCP server definitions and agent entry
points, and produces a manifest and A2A agent card. `donkey publish` registers
them with Exchange / Agent Registry. A GitHub Action runs both on every merge to
`main`.

**The registry becomes a consequence of the code, not a chore.** A support agent
with six tools registered by hand has a stale tool list within a week; with the
Action, every merge updates it.

  **This complements MuleSoft's Agent Scanners.** Agent Scanners discover agents
  from Agentforce, Bedrock, Vertex AI, and Copilot Studio at **runtime**. Scan &
  publish is the **design-time / CI** complement: it registers agents built in
  plain Python that no cloud scanner can see.

## Scope: code-first assets

Publication is for assets that **originate in your code**:

- an MCP server written in Python or TypeScript,
- an agent exposed over [A2A](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md),
- an agent exposed as a tool without an A2A surface.

It is not for assets the platform already owns. An MCP server created by MCP
Bridge from an existing API is already in Exchange; publishing a second,
code-derived descriptor would create two catalog entries for one capability.

### Collision check

Before publishing, the SDK searches Exchange for an existing asset with the
same endpoint, name, or derived tool signature. On a probable match it
**refuses** and prints the existing asset's coordinates. Override with an
explicit `--allow-duplicate`, which logs at `WARNING`.

## The `Publication` object

```python
from donkey_kit import Contact, Publication, PublicationAssetType

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

`asset_type` determines which descriptor is required and how it is generated:

| `asset_type` | Descriptor | Generated from |
|---|---|---|
| `MCP_SERVER` | MCP tool manifest — server info, tool names, descriptions, JSON Schema inputs | live `tools/list` against the running server |
| `A2A_AGENT` | A2A Agent Card | declared skills, endpoint, auth schemes, input/output modes |
| `AGENT` | agent descriptor (no A2A surface) | framework introspection, best-effort |
| `API` | OpenAPI / AsyncAPI | user-supplied file; no generation |

## `descriptor="auto"` — deriving the spec from code

Hand-maintained catalog descriptors go stale within a sprint, so generation is
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
descriptor="auto:static"   # AST only — lowest fidelity, no code execution
descriptor="auto:check"    # generate, diff against committed file, fail on mismatch
```

- **`auto:live`** starts the server, performs the MCP initialize handshake, and
  calls `tools/list` (plus `resources/list` and `prompts/list`). It is exactly
  what a client sees, but the server must actually run, with whatever
  credentials and network that needs.
- **`auto`** (the default) imports your module, locates the tool and agent
  objects, and reads their already-computed schemas. No server, no network,
  works in CI. Importing user code executes it, so tool definitions must be

  ```toml
  [publication.entrypoints]
  "hr-tools-mcp" = "acme.hr.server:mcp"        # module:attribute
  "hr-agent"     = "acme.hr.agent:build_agent" # a zero-arg factory also works
  ```

- **`auto:static`** parses decorators, signatures, and docstrings via AST
  without executing anything — for environments where importing user code is
  unacceptable. It cannot see tools registered in a loop, from config or a
  database, behind a feature flag, attached dynamically at startup, or built
  from imported/generated pydantic models, so it emits a completeness warning
  whenever it hits a pattern it cannot resolve. It is never the default.
- **`auto:check`** generates the descriptor, diffs it against the committed
  file, and fails on mismatch — useful as a CI gate.

`donkey publish --cross-check` runs `auto` and `auto:live` and diffs them. A
disagreement means either dynamic registration the object graph does not
reflect, or a broken framework adapter.

  Type hints give the **shape**, not the **meaning** — `department: str` becomes
  `{"type": "string"}` and says nothing about which departments are valid, or
  when to use this tool over a similar one. `auto` fails publication on a
  description that is tautological or missing, and `preview()` reports
  description quality.

## Related

- [CLI & decorators](https://donkey-development-kit.github.io/donkey-development-kit/cli.md) — mark tools with `@donkey.tool` today.
- [A2A agents](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md) — serve and expose the agent whose card you publish.
- [Tool access](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md) — discover and bind published tools from Exchange.
