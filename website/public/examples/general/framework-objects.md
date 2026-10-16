# Framework objects & model handles

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

No adapter returns a wrapper. `donkey.langgraph.chat_model(...)` hands back a
real `langchain_openai.ChatOpenAI`, so everything your framework can do with a
model still works and nothing new appears in your stack traces. LangGraph and
the OpenAI Agents SDK are the deep, conformance-gated adapters; the other six
are supported at `connection_kwargs()` — the SDK gives you the base URL,
headers and client configuration, and you pass them to the framework's own
constructor. The
companion example covers model handles: `resolve()` gives a local capability
handle, and `list_models(live=True)` raises a `ConfigError` explaining that the
proxy has no catalog endpoint rather than guessing one.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 07 | `resolve()` capability handles, `list_models(live=True)` raising `ConfigError`, and config validation listing every missing field at once | Nothing |
| Narrative demo 08 | One factory call per framework and what came back, then `connection_kwargs()` for the shallow adapters | Nothing — objects are constructed, no network calls |

## Run it

```bash
make demo N=07
make demo N=08
```

```text
════════════════════════════════════════════════════════════════════════════════════════
Demo 07 — model handles and honest gaps
What the SDK does when the platform has no endpoint for what you asked.
════════════════════════════════════════════════════════════════════════════════════════

Run context
───────────
  target                 offline — no gateway, no simulator, no credentials
  output masking         on

[1] resolve() — a local capability handle for a known model id

    handle = donkey.llm.resolve("gpt-4o")
    handle.capabilities

  gpt-4o                 ModelCapabilities(function_calling=True, vision=True, json_output=True, is_heuristic=True)
  gpt-4o-mini            ModelCapabilities(function_calling=True, vision=True, json_output=True, is_heuristic=True)
  o3                     ModelCapabilities(function_calling=True, vision=False, json_output=False, is_heuristic=True)
  claude-3-5-sonnet      ModelCapabilities(function_calling=True, vision=False, json_output=False, is_heuristic=True)
  something-unknown-9    ModelCapabilities(function_calling=True, vision=False, json_output=False, is_heuristic=True)

  These are heuristics derived from the model id, and the SDK says so rather than
  implying it asked the gateway. They are useful for routing decisions in your own code;
  they are not a governed catalog.

[2] list_models(live=True) — the honest failure

    await donkey.llm.list_models(live=True)

  raised                 ConfigError

  The governed LLM proxy exposes no /models endpoint (GET /models → 404, verified
  docs/verified-apis.md §2): it only routes requests carrying `model` in the body. Live
  model listing is not available from the proxy. Use resolve(model_id) or source the
  catalog from Exchange/provider config.

  PASS  It names the verified absence and points at the alternative, instead of guessing a /models path that would 404 in your sandbox.

[3] The same discipline applied to configuration
  The most common reason someone abandons an SDK in the first five minutes is the one-
  missing-variable-per-run loop: fix a variable, re-run, discover the next one. So
  validation reports everything at once.

    DonkeyConfig(llm_proxy_url="https://…").validated(need="llm")

    Configuration for 'llm' is incomplete. Missing:
      - llm_proxy_client_id (env DONKEY_LLM_PROXY_CLIENT_ID)
      - llm_proxy_client_secret (env DONKEY_LLM_PROXY_CLIENT_SECRET)
    Set them via kwargs, environment variables, or .donkey-kit.toml (secrets in .donkey-kit.local.toml).

  Two missing fields, one error, each naming the environment variable that sets it. And
  note the LLM proxy credential is validated separately from the Anypoint control-plane
  one — a developer may legitimately have proxy access and no Exchange access.

  When the failure is live rather than a missing variable — wrong URL, wrong
  credentials, or a model the allow-list does not include — `donkey doctor` is the CLI
  that distinguishes those three. It reuses the same remediation strings the typed
  errors carry (demo 02).

────────────────────────────────────────────────────────────────────────────────────────
```

```text
════════════════════════════════════════════════════════════════════════════════════════
Demo 08 — native framework objects
Two deep adapters, six at connection_kwargs(), and no wrappers anywhere.
════════════════════════════════════════════════════════════════════════════════════════

Run context
───────────
  target                 offline — no gateway, no simulator, no credentials
  output masking         on

[1] One call per framework, and what came back

  langgraph        deep — conformance-gated
    donkey.langgraph.chat_model(…)
  PASS  returned langchain_openai.chat_models.base.ChatOpenAI

  adk              connection_kwargs()
    donkey.adk.model(…)
    not installed: pip install "donkey-kit[adk]"

  strands          connection_kwargs()
    donkey.strands.model(…)
    not installed: pip install "donkey-kit[strands]"

  agent_framework  connection_kwargs() + policy_middleware(); model=
    donkey.agent_framework.chat_client(…)
    not installed: pip install "donkey-kit[agent_framework]"

  openai_agents    deep; connection_kwargs() → {openai_client}
    donkey.openai_agents.model(…)
  PASS  returned agents.models.openai_chatcompletions.OpenAIChatCompletionsModel

  anthropic        connection_kwargs(); native route needs Format=Anthropic
    donkey.anthropic.client()
    not installed: pip install "donkey-kit[anthropic]"

  crewai           connection_kwargs()
    donkey.crewai.llm(…)
    not installed: pip install "donkey-kit[crewai]"

  llamaindex       connection_kwargs()
    donkey.llamaindex.llm(…)
    not installed: pip install "donkey-kit[llamaindex]"

[2] connection_kwargs() — the surface that actually carries the roster

    kwargs = donkey.strands.connection_kwargs()
    SomeFrameworkModel(model="gpt-4o", **kwargs)

  langgraph.connection_kwargs() 
    base_url                       https://demo-gateway.example.invalid/openai-sdk/
    api_key                        client-id-enforced
    default_headers.client_id      <redacted> (36 chars)
    default_headers.client_secret  <redacted> (40 chars)
    http_async_client              <donkey_kit.core.transport.httpx2._ReusableBridgedClient object at 0x10ac683b0>
    http_client                    <donkey_kit.core.transport.httpx2._ReusableBridgedSyncClient object at 0x1143e3e30>
    max_retries                    0
    use_responses_api              False

  Same base URL, same default client_id / client_secret pair (llm_proxy_auth='client-
  id'), handed to the framework's own constructor. The model-wallet JWT ingress is a
  different mode — demo 13. Bringing a framework up to the deep bar is demand-driven and
  happens one at a time, so this is not a stepping stone that everything is queued
  behind — it is the supported surface.
  LangGraph and the Agents SDK are the adapters held to the conformance bar. LangGraph
  keeps ChatOpenAI on /chat/completions (use_responses_api=False), the one route every
  upstream behind an OpenAI-format proxy serves; pass use_responses_api=True to opt into
  /responses on an OpenAI-routed proxy.

[3] Agents SDK: connection_kwargs() is one governed client object

    kwargs = donkey.openai_agents.connection_kwargs()
    OpenAIChatCompletionsModel(model="gpt-4o", **kwargs)

  keys                   ['openai_client']
  openai_client          openai.AsyncOpenAI
  base_url               https://demo-gateway.example.invalid/openai-sdk/
  PASS  one key — header and transport injection travel as one object

  The Agents SDK does not take loose URL/header kwargs. It takes a client. So
  connection_kwargs() returns that client, and model() is
  OpenAIChatCompletionsModel(model=..., **those kwargs). donkey.openai() is still the
  raw factory; donkey.openai_agents is this adapter.

[4] Agent Framework: policy_middleware() ends the run on a refusal

    agent = Agent(
        client=donkey.agent_framework.chat_client("gpt-4o"),
        middleware=[donkey.agent_framework.policy_middleware()],
    )
    with donkey.simulate(PIIDetected):
        await agent.run("my email is jane@example.com")   # → PIIDetected

  agent-framework is not installed — pip install "donkey-kit[agent_framework]"
  The behaviour is still the point: with the middleware, a policy refusal ends
  agent.run() as the typed PIIDetected, not as the framework's generic client error, and
  the loop does not retry it.

What is and is not verified here
────────────────────────────────
  The proxy contract these objects are configured against is live-verified: the base URL
  shape, the credential header pair, the rejection shapes. The adapters themselves are
  held to three bars (docs/verified-apis.md §8):
  • Conformance-tested against the simulator: the raw client, LangGraph and the OpenAI
    Agents SDK.
  • Signature-confirmed offline: every other adapter, ADK's model() included. The SDK's
    scripts/verify_frameworks.py builds each native object against the installed
    framework; Agent Framework's model= kwarg (not model_id) is confirmed that way
    against 1.19.0.
  • Live-verified: ADK's gemini(), through a Format=Gemini proxy.

  The adapters build the framework's native object directly. They refuse with 'blocked
  on verification' only when the installed framework version lacks the class or field
  the adapter depends on: an Agent Framework class rename, or ADK's gemini() before
  google-adk 2.4.

────────────────────────────────────────────────────────────────────────────────────────
```

Narrative demo 08 uses obviously-fake config, so it needs no credentials.
Frameworks that are not installed are reported with their exact `pip install`
line.

## Key code

The roster narrative demo 08 walks — attribute on `Donkey`, factory method, and
depth:

```python
ROSTER = [
    ("langgraph", "chat_model", True, "deep — conformance-gated"),
    ("adk", "model", True, "connection_kwargs()"),
    ("strands", "model", True, "connection_kwargs()"),
    ("agent_framework", "chat_client", True, "connection_kwargs() + policy_middleware(); model="),
    ("openai_agents", "model", True, "deep; connection_kwargs() → {openai_client}"),
    ("anthropic", "client", False, "connection_kwargs(); native route needs Format=Anthropic"),
    ("crewai", "llm", True, "connection_kwargs()"),
    ("llamaindex", "llm", True, "connection_kwargs()"),
]
```

For the shallow adapters, `connection_kwargs()` is the whole supported surface:

```python
kwargs = donkey.strands.connection_kwargs()
SomeFrameworkModel(model="gpt-4o", **kwargs)
```

An adapter or `LLMClient` you construct directly owns any blocking transport it
builds on demand. Call its `close()` (or `await aclose()`) when finished; neither
method closes the async client you passed in. Adapters obtained from `Donkey`
use the `Donkey` runtime's blocking client instead, so close the `Donkey` handle.

Model handles and the missing catalog (narrative demo 07):

```python
handle = donkey.llm.resolve("gpt-4o")
handle.capabilities

await donkey.llm.list_models(live=True)   # raises ConfigError
```

And config validation reports every missing field in one error, each naming
the environment variable that sets it:

```python
DonkeyConfig(llm_proxy_url="https://…").validated(need="llm")
```

  `donkey.openai_agents` is the OpenAI Agents SDK adapter; `donkey.openai()` is
  the raw OpenAI client factory. The LangGraph adapter sets
  `use_responses_api=False`, so `ChatOpenAI` calls the `/chat/completions` route. The
  adapters build the native object directly. They refuse with "blocked on
  verification" only when the installed framework version lacks the class or
  field the adapter depends on (for example, `gemini()` on google-adk older than
  2.4). See [Model access](https://docs.donkey-kit.dev/frameworks.md#supported-frameworks) for each adapter's
  verification status.

`resolve()` capabilities are heuristics derived from the model id, not a
governed catalog. The gateway returns 404 for `GET /models` because
model-based routing only routes requests that already carry `model` in the
body. When a live call fails — wrong URL, wrong credentials, or a model the
allow-list does not include — [`donkey doctor`](https://docs.donkey-kit.dev/cli.md) tells those apart.

**Learn more:** [Model access](https://docs.donkey-kit.dev/frameworks.md) · [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md) · [CLI & decorators](https://docs.donkey-kit.dev/cli.md)

**Source:**
[narrative demo 07](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/07_model_handles) ·
[narrative demo 08](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/08_framework_objects)
