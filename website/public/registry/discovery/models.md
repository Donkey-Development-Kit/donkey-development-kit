# Discovery for models

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Models are not discovered the way MCP tools are. The governed LLM proxy has
**no** catalog endpoint: `GET /models` returns `404`, because model-based
routing only routes requests that carry `model` in the body. So DDK does not
guess a catalog path. These are what is available instead.

| You want | Use | Notes |
|---|---|---|
| A handle for a model id you already know | `donkey.llm.resolve(model_id)` | A heuristic `ModelHandle`, built without a network call. |
| A list of the proxy's models | `await donkey.llm.list_models()` | Not available today. With or without `live=True` it raises `ConfigError`, pointing you at `resolve()` or Exchange. |
| What an LLM proxy or custom provider *declares* | `donkey.registry.llm_metadata()` | Reads the `llm-metadata` file of an Exchange `llm` asset; see below. |

How the gateway then picks a provider for the model you named is on
[Routing & fallback](https://docs.donkey-kit.dev/frameworks/routing.md), and the version rules for each
framework are on [Version compatibility](https://docs.donkey-kit.dev/frameworks/compatibility.md).

## Reading an LLM asset's metadata

`donkey.registry.llm_metadata()` is Live. It reads the
`llm-metadata` file of an `llm` asset version, an LLM proxy or a custom LLM
provider, and returns what it declares:

```python
meta = await donkey.registry.llm_metadata("<group-id>/my-llm-provider/1.0.0")
if meta is not None:
    print(meta.platform, meta.input_format, meta.models)
```

`LlmMetadata` has `platform`, `name`, `input_format`, `models` (the model IDs)
and `capabilities` (the file's object as published). The read is tolerant: it
ignores fields it does not know, and a field the file leaves out or sets to the
wrong type comes back as `None` or empty, never a guess. A DDK LLM proxy's file
sets only `platform`. The read returns `None` for a version with no
`llm-metadata` file, and raises `RegistryError` when Exchange has no such
version. It needs `read:exchange`, and the file download carries no credential.

## Related

- [Discovery for MCP tools](https://docs.donkey-kit.dev/registry/discovery/mcp.md#registry-search) — `search()` also finds `"llm"` assets by type.
- [Model gateway policies](https://docs.donkey-kit.dev/governance/gateway-policies/models.md) — what the gateway enforces on model calls.
- [Models](https://docs.donkey-kit.dev/frameworks.md) — the native model object each framework gets.
