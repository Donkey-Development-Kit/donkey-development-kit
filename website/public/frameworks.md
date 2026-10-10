# Model access

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Governed model access from eight agent frameworks. Each adapter returns the
framework's **own native object**, pointed at your Omni Gateway LLM proxy with
consumer auth and attribution headers already set. Nothing wraps the object you
get back, and every page shows the plain-framework code you can switch to at
any time.

  **Using TypeScript or another language?** The SDK is Python. From TypeScript
  or any other language, call the proxy's OpenAI-compatible HTTP API directly —
  every framework page has a **TypeScript** tab showing the official `openai`
  npm client (or `@anthropic-ai/sdk` for Anthropic) pointed at the same proxy.

## Supported frameworks

LangGraph is the **deep** adapter: it runs the full conformance suite in CI,
including graph-level scenarios against a compiled `StateGraph`. The other
seven are **supported at `connection_kwargs()`**: the governed connection
settings are tested, and each exposes factory methods that return the native
object.

Each card shows what has been proven about that adapter, in the terms the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md) uses:

| Status | Means |
|---|---|
| Conformance-tested | Runs the conformance suite against the [local simulator](https://docs.donkey-kit.dev/simulator.md) in CI. |
| Live-verified | Has made a real round-trip through a governed proxy. |
| Signature-confirmed | The factory builds the native object against the installed framework, checked offline by `python scripts/verify_frameworks.py`. No live round-trip yet. |

The proxy data plane every adapter calls (base URL, credential headers,
streaming, rejection shapes) is live-verified.

  
    `chat_model()` → `langchain_openai.ChatOpenAI`
  
  
    `model()` → `google.adk … LiteLlm` (signature-confirmed); `gemini()` → `google.adk.models.Gemini` (live-verified)
  
  
    `model()` → `strands … OpenAIModel`
  
  
    `chat_client()` → Agent Framework chat client
  
  
    `model()` → `agents.OpenAIChatCompletionsModel`
  
  
    `client()` → `anthropic.AsyncAnthropic`
  
  
    `llm()` → `crewai.BaseLLM` (`OpenAICompletion`)
  
  
    `llm()` → `OpenAILike` (`is_chat_model=True`)
  
  
    `donkey.llm.client()` → `openai.AsyncOpenAI` (or `OpenAI` with `sync=True`)
  

## The shape is the same everywhere

```bash
pip install "donkey-kit[<framework>]"
export DONKEY_LLM_PROXY_URL=…  DONKEY_LLM_PROXY_CLIENT_ID=…  DONKEY_LLM_PROXY_CLIENT_SECRET=…
```

```python
from donkey_kit import Donkey
async with Donkey.from_env() as donkey:
    # donkey.<framework>.<factory>(...), here LangGraph's chat_model:
    model = donkey.langgraph.chat_model("gpt-4o")   # native object at the proxy
```

Each framework page shows the factory name, the native class you get back, the
three ways to construct it, and **the manual equivalent** — the plain framework
constructor call the factory makes for you.

### Printing `connection_kwargs()` hides the secrets

`connection_kwargs()` returns a `dict` that prints `'***'` in place of
`api_key`, the `client_secret` header, `Authorization` and other credential
keys, including inside nested header mappings. The framework still receives the
real values, and `json.dumps`, `dict(...)`, `{**kwargs}` or `kwargs.items()`
still expose the top-level `api_key`. Some framework objects built from the
kwargs (LangGraph's `ChatOpenAI`, LlamaIndex's `OpenAILike`, CrewAI's
`OpenAICompletion`) print credentials themselves. See
[What printed output hides](https://docs.donkey-kit.dev/reference/configuration.md#what-printed-output-hides)
for exactly what is and isn't masked.

Where the framework's dependencies are installed, `connection_kwargs()` also
carries the SDK's HTTP client in the form that framework takes: `http_client`
and `http_async_client` (LangGraph), `http_client` and `async_http_client`
(LlamaIndex), `async_client` (MS Agent Framework), `client` (ADK's `model()`),
or an `interceptor` (CrewAI). Pass them through with the rest of the kwargs.
Each HTTP client is a non-owning view of the SDK's shared client (on `openai`
3.x, the OpenAI kwargs carry an `httpx2` bridge onto it instead): it sends
through the shared client, and closing it (as Strands does after every call, or
`async with` on an OpenAI client) leaves the shared client open. Only
`donkey.aclose()` / `donkey.close()` end the connection pool. `donkey.http_client()`
returns the same view if you build a framework client by hand.

```python
kwargs = donkey.llamaindex.connection_kwargs()
print(kwargs["default_headers"])      # {'client_id': 'my-client-id', 'client_secret': '***'}
OpenAILike(model="gpt-4o", **kwargs)  # receives the real secret
```

### What each factory gets: `capabilities()`

Every adapter reports what its factories get from the SDK.
`donkey.<framework>.capabilities("<factory>")` returns a frozen
`AdapterCapabilities`. With no argument, it returns the default factory's, the
one `connection_kwargs()` configures. Each factory reports its own, so ADK's
`model()` and `gemini()` answer separately.

| Field | Means |
|---|---|
| `transport` | `"shared"`: requests go through the SDK's shared client. `"framework"`: the framework builds its own clients (CrewAI), so `jwt` and `bearer` auth are refused with `ConfigError`. |
| `sync` | The native object's blocking calls (`invoke()`, `complete()`) also go through the SDK. In `jwt` and `bearer` mode, each blocking call raises `ConfigError`, because the token is async-only. |
| `streaming` | `False` where the governed connection turns streaming off (Strands, `stream=False`). |
| `typed_refusals` | A gateway refusal reaches you as a typed exception through `donkey.run()` or `typed_refusals()`. |
| `observes_last_call` | A call through this object populates `donkey.last_call`. |

```python
caps = donkey.adk.capabilities("gemini")
print(caps.transport, caps.observes_last_call)   # shared True
```

The conformance exemptions are checked against these values, so the
`capabilities()` an adapter reports and the exemptions it records always
agree.

In `jwt` or `bearer` mode, every adapter's `connection_kwargs()` raises
`ConfigError` when the `Donkey` has no `llm_auth` provider. The token is added
only through that provider, so without one every call would fail with a 401.

## Match the adapter to your proxy's wire format

Every adapter on this page except Anthropic and ADK's `gemini()` — and the raw
`donkey.llm.client()` — speaks the **OpenAI wire format**. The format your proxy accepts is the
**Format** (OpenAI / Anthropic / Gemini) chosen when the proxy was provisioned.
It is a property of the proxy, not an SDK setting, so there is no config field
for it: pick the adapter that matches your proxy.

| Proxy ingress **Format** | Use |
|---|---|
| **OpenAI** | `donkey.llm.client()` or any framework adapter. Default DDK proxies are `Format=OpenAI`. |
| **Anthropic** | `donkey.anthropic.client()` (native `AsyncAnthropic`). The proxy serves the native Messages route at `POST /<base-path>/v1/messages`; OpenAI-shape `/chat/completions` returns 404. |
| **Gemini** | `donkey.adk.gemini("gemini-2.5-flash")` (ADK's native `Gemini` model — see [Native Gemini](https://docs.donkey-kit.dev/frameworks/adk.md#native-gemini)). The proxy serves `POST /<base-path>/models/<model>:generateContent` and `:streamGenerateContent`. There is no standalone `google-genai` adapter; you can also reach Gemini as an upstream provider behind an OpenAI-format proxy (see below). |

  **Ingress Format is not the same as the upstream provider.** The ingress
  Format is the wire protocol *your request* speaks to the proxy. The upstream
  provider is the model the proxy routes *to* after accepting it. A
  model-based-routing proxy with OpenAI ingress already fans out to OpenAI,
  Gemini, Azure OpenAI, Bedrock Anthropic, and NVIDIA upstreams, selected by the
  `model` value in your request body.

  So to use Gemini or Claude models you don't need a Gemini- or Anthropic-format
  proxy: send an OpenAI-format request naming that model to an OpenAI-format
  proxy.

### Decision models: TypeSafe Jev Roadmap

[TypeSafe Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
is a *System One* model: instead of generating text it answers typed questions
(yes/no probability, a choice among options, or a score on a scale) with
calibrated confidence. Its API is **not OpenAI-compatible**, so none of the
adapters above, and no OpenAI-format proxy, can call it. Planned support
returns TypeSafe's own client pointed at Jev behind Omni Gateway, with the same
auth, correlation, typed refusals, spans, budget and simulator support as LLM
calls.

## Injection depth differs by framework

How much of the SDK's HTTP layer reaches the request depends on what each
framework's constructor accepts. With **header injection**, the proxy auth and
attribution headers are sent. With **transport injection**, the SDK's shared
HTTP client is also used, which adds per-run correlation IDs, retries, spans,
`donkey.last_call`, the `jwt`-mode JWT, and
[credentials only to checked endpoints](https://docs.donkey-kit.dev/reference/configuration.md#credentials-go-only-to-checked-endpoints).

| Framework | Header injection | Transport injection | Notes |
|---|---|---|---|
| LangGraph | ✅ | ✅ | `default_headers` plus the SDK's async client (`ainvoke`) and blocking client (`invoke`). |
| Strands | ✅ | ✅ | Via `client_args`. |
| OpenAI Agents SDK | ✅ | ✅ | The adapter builds the `AsyncOpenAI` client itself. |
| Anthropic SDK | ✅ | ✅ | Returns a bare `client()`, not a model-bound object. On `anthropic` 1.0 and later, transport injection goes through a bridged `httpx2` client — see the [Anthropic page](https://docs.donkey-kit.dev/frameworks/anthropic.md). |
| LlamaIndex | ✅ | ✅ | Via `http_client` (sync) and `async_http_client`. `is_chat_model=True` is forced. |
| MS Agent Framework | ✅ | ✅ | Via an `async_client` built on the SDK's client. |
| Google ADK — `model()` | ✅ (`extra_headers`) | ✅ | LiteLLM gets a pre-built OpenAI `client` that sends through the SDK's client. |
| Google ADK — `gemini()` | ✅ | ✅ | Via `HttpOptions.httpx_async_client`, on a `Format=Gemini` proxy. |
| CrewAI | ✅ (`extra_headers`) | ❌ | CrewAI's native OpenAI provider builds its own HTTP client: correlation is per client and `donkey.last_call` is not populated. An `interceptor` keeps credentials to checked endpoints. |

## Retries happen once, in the SDK

The SDK's transport retries `502`, `503` and `504` with backoff, up to
`max_retries` times, and never retries a `4xx`. A `502` or `504` on a model
call is retried only with `retry_model_calls_on_gateway_errors`, since the
provider may already have billed it. On the proxy a `429` is a
token-budget refusal (`TokenBudgetExceeded`), so sending it again would only
spend more of a budget that is already gone. Every adapter therefore turns off
the provider SDK's own retries (`max_retries=0`). The transport also marks every
final `4xx` with `x-should-retry: false`, which the `openai` and `anthropic`
SDKs honour, so a client you build yourself from `connection_kwargs()` with its
own retry setting doesn't re-send a refusal either.

Some frameworks retry above the provider SDK, where the SDK can't reach:

| Framework | A budget `429` is sent | A persistent `503` is sent | What to do |
|---|---|---|---|
| LangGraph, OpenAI Agents SDK, Anthropic SDK, LlamaIndex, MS Agent Framework, Google ADK | once | `max_retries + 1` times | Nothing. |
| Strands | once | `max_retries + 1` times | Nothing with `donkey.strands.model()`. A model built from `connection_kwargs()` needs `Agent(retry_strategy=None)`. See the [Strands page](https://docs.donkey-kit.dev/frameworks/strands.md#notes). |
| CrewAI | 3 times | once | No setting turns it off. See the [CrewAI page](https://docs.donkey-kit.dev/frameworks/crewai.md#notes). |

Transport injection also decides whether `jwt` mode works: the rotating JWT is
attached only by the SDK's shared async client. CrewAI can't carry it, so it
raises `ConfigError` in `jwt` mode. Sync calls such as LangGraph's `invoke()`
also raise `ConfigError` instead of sending. See the
[`jwt` mode note](https://docs.donkey-kit.dev/reference/configuration.md#jwt--model-wallet-auth-mode).
[`bearer` mode](https://docs.donkey-kit.dev/reference/configuration.md#bearer-token-auth-mode) has the same
reach; there, CrewAI raises `ConfigError` instead of sending no token.

A URL override passed to a factory (`base_url`, `api_base`, `openai_api_base`,
or Strands' `client_args["base_url"]`) must pass the same
[`https://` rule](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https) as the
configured proxy URL; it then receives the configured credentials.

See the [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md) for how each constructor
signature the adapters depend on is checked.
