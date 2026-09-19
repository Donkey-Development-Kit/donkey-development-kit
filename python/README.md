<p align="center">
  <img src="https://raw.githubusercontent.com/Donkey-Development-Kit/donkey-development-kit/main/brand/ddk-logo-stacked-black.png" alt="Donkey Development Kit (DDK)" width="180" />
</p>

# Donkey Development Kit

An SDK for consuming **Agent Fabric** capabilities — governed model access,
governed tool access, and provisioning-as-code — from your own agent framework,
in your own IDE, without adopting Mule.

> **Project status — alpha, pre-release.** This is `v0.1.0.dev0`
> (`Development Status :: 3 - Alpha`). The **LLM data plane is live-verified**;
> most other surfaces are verification-gated (see
> [What's verified](#whats-verified-03) below). **Not yet published to PyPI** —
> [install from source](#install). **Unofficial:** an independent project,
> **not** affiliated with or endorsed by Salesforce or MuleSoft.

> **Already integrated the pre-rebrand SDK?** The move to Donkey Development Kit
> is a clean break — no import shims, env fallbacks, or OpenTelemetry dual-emit.
> The [migration guide](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/MIGRATION.md)
> maps every renamed import, class, CLI, config key, and environment variable,
> and calls out the breaking OpenTelemetry attribute-namespace change.

> ### Support & trademark statement (please read — §0.4)
>
> **"Agent Fabric" is a MuleSoft (Salesforce) product name, not a generic
> term.** `MuleSoft`, `Anypoint`, `Omni Gateway`, and `Agent Fabric` are
> Salesforce trademarks.
>
> **Maintainer & support.** This is an **independent, community-maintained**
> project, published under the org-scoped `Donkey-Development-Kit` name — it is **not**
> affiliated with, endorsed by, or supported by Salesforce or MuleSoft. It is
> provided **as-is, without warranty of any kind**; the maintainers triage issues
> and pull requests on a **best-effort basis, with no SLA**. Because it ships
> under a distinct, org-scoped name, only the descriptive form ("an SDK for
> MuleSoft Agent Fabric") appears in prose — the package does not represent itself
> as a first-party, official-status SDK.
>
> Licensed under [Apache-2.0](LICENSE). See
> [`docs/unsupported-boundary.md`](docs/unsupported-boundary.md) for exactly
> which platform APIs this SDK calls and their support classification.

## Documentation

Two audiences, two doc sets:

- **Use the SDK** → the documentation site:
  **<https://donkey-development-kit.github.io/donkey-development-kit/>**. Install and
  configure, per-framework model access, the governed error taxonomy, and what
  to trust today — everything you need to point your agent at a governed proxy.
- **See it run** → runnable demos live in the companion repo
  **[donkey-development-kit-demos](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos)**:
  the framework-free client, native framework objects, the governed error
  taxonomy, and the screen-recording scripts.
- **Understand or contribute to the repo:**
  - [`ARCHITECTURE.md`](ARCHITECTURE.md) — how the SDK is built: the layered
    stack, the framework-free core, verification discipline, the error taxonomy,
    and framework tiering.
  - [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to work in the repo: the
    branch/PR/release workflow, the testing strategy, coding conventions, and
    the docs-sync rule.
  - [`docs/verified-apis.md`](docs/verified-apis.md) — the verification ledger:
    the single source of truth for what is confirmed against a real sandbox and
    what is still blocked.

## Install

> **Not yet published to PyPI.** Until the first release is cut, install from
> source:

```bash
git clone https://github.com/Donkey-Development-Kit/donkey-development-kit.git
cd donkey-development-kit/python
pip install -e ".[llm,langgraph]"   # base + raw client + one framework
```

Extras are one per framework (`langgraph`, `adk`, `strands`, `agent_framework`,
`openai-agents`, `anthropic`, `crewai`, `llamaindex`) plus `mcp`, `a2a`, `otel`, `cli`,
`local`, `test` (the [conformance pytest plugin](https://donkey-development-kit.github.io/donkey-development-kit/testing) —
`pytest --donkey-conformance --agent=my_app.agent:build`), and `all`.
Configuration and first-agent walkthroughs live on the
[documentation site](https://donkey-development-kit.github.io/donkey-development-kit/).

## Framework support

The roster is deliberately **one deep, seven shallow** (`BG §1.8`): one adapter
held to the full conformance bar, the rest supported through the three-line
`connection_kwargs()` escape hatch. Every framework below returns its framework's
**own native object** — never a wrapper.

| Tier | Frameworks | What it means |
| --- | --- | --- |
| **Conformance-tested** | The raw client (`donkey.llm.client()`) and **LangGraph** | Held to the conformance suite in CI — the governed contract is proven end to end. |
| **Supported via `connection_kwargs()`** | Google ADK, Strands, Microsoft Agent Framework, OpenAI Agents SDK, Anthropic SDK, CrewAI, LlamaIndex | Governed kwargs verified at the `connection_kwargs()` level, not conformance-tested. |

`connection_kwargs()` works for all eight; a second deep adapter is promoted from
demand evidence, one at a time (#223/#244) — never guessed up front. See the
[framework pages](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/)
for each.

## What's verified (§0.3)

The **LLM data plane** — governed model access through the Omni Gateway proxy —
is live-verified against a real Anypoint sandbox, and both the framework-free
client and the framework adapters are wired to that verified contract — LangGraph
is held to the conformance suite, the other seven are supported at the
`connection_kwargs()` level (see [Framework support](#framework-support)).
Everything still gated raises `NotImplementedError("blocked on verification: …")`
rather than guessing at an unverified endpoint, header, or class name — that
currently includes Exchange→MCP tool discovery, the provisioning control-plane,
and the exact framework adapter class names/kwargs.

The discipline behind this is documented in
[`ARCHITECTURE.md` → Verification discipline](ARCHITECTURE.md#verification-discipline-03);
the row-by-row worklist is [`docs/verified-apis.md`](docs/verified-apis.md).

## Conformance exemptions

The [conformance plugin](https://donkey-development-kit.github.io/donkey-development-kit/testing)
holds the SDK to the same bar it asks of your agent. Where a framework
legitimately cannot satisfy a scenario, the reason is asserted in code
(`KNOWN_LIMITATIONS`) and published here as credibility — never a silent skip
(§8.1):

| Framework | Scenario | Why it's exempt |
| --- | --- | --- |
| ADK, CrewAI | correlation ID propagated | LiteLLM owns the transport, so the SDK's `httpx` client cannot be injected — the correlation ID ends up per-client, not per-run. A LiteLLM logger callback may recover trace correlation later. |
