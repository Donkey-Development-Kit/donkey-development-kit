# Model access

Live

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

  
    `chat_model()` → `langchain_openai.ChatOpenAI`
  
  
    `model()` → `google.adk … LiteLlm`
  
  
    `model()` → `strands … OpenAIModel`
  
  
    `chat_client()` → Agent Framework chat client
  
  
    `model()` → `agents.OpenAIChatCompletionsModel`
  
  
    `client()` → `anthropic.AsyncAnthropic`
  
  
    `llm()` → `crewai.LLM` (LiteLLM-backed)
  
  
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
    model = donkey.<framework>.<factory>("gpt-4o")   # native object at the proxy
```

Each framework page shows the factory name, the native class you get back, the
three ways to construct it, and **the manual equivalent** — the plain framework
constructor call the factory makes for you.

## Match the adapter to your proxy's wire format

Every adapter on this page except Anthropic — and the raw `donkey.llm.client()`
— speaks the **OpenAI wire format**. The format your proxy accepts is the
**Format** (OpenAI / Anthropic / Gemini) chosen when the proxy was provisioned.
It is a property of the proxy, not an SDK setting, so there is no config field
for it: pick the adapter that matches your proxy.

| Proxy ingress **Format** | Use |
|---|---|
| **OpenAI** | `donkey.llm.client()` or any framework adapter. Default DDK proxies are `Format=OpenAI`. |
| **Anthropic** | `donkey.anthropic.client()` (native `AsyncAnthropic`). The proxy serves the native Messages route at `POST /<base-path>/v1/messages`; OpenAI-shape `/chat/completions` returns 404. |
| **Gemini** | No SDK adapter. Point a native `google-genai` client at `…/models/<model>:generateContent` yourself, or reach Gemini as an upstream provider behind an OpenAI-format proxy (see below). |

  **Ingress Format is not the same as the upstream provider.** The ingress
  Format is the wire protocol *your request* speaks to the proxy. The upstream
  provider is the model the proxy routes *to* after accepting it. A
  model-based-routing proxy with OpenAI ingress already fans out to OpenAI,
  Gemini, Azure OpenAI, Bedrock Anthropic, and NVIDIA upstreams, selected by the
  `model` value in your request body.

  So to use Gemini or Claude models you don't need a Gemini- or Anthropic-format
  proxy: send an OpenAI-format request naming that model to an OpenAI-format
  proxy.

## Injection depth differs by framework

How much of the SDK's HTTP layer reaches the request depends on what each
framework's constructor accepts. With **header injection**, the proxy auth and
attribution headers are sent. With **transport injection**, the SDK's shared
HTTP client is also used, which adds per-run correlation IDs and
`donkey.last_call`.

| Framework | Header injection | Transport injection | Notes |
|---|---|---|---|
| LangGraph | ✅ | ✅ | `default_headers` plus a custom async client. |
| Strands | ✅ | ✅ | Via `client_args`. |
| OpenAI Agents SDK | ✅ | ✅ | The adapter builds the `AsyncOpenAI` client itself. |
| Anthropic SDK | ✅ | ✅ | Returns a bare `client()`, not a model-bound object — see the [Anthropic page](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/anthropic.md). |
| LlamaIndex | ✅ | ❌ | Static `default_headers` snapshot: no per-run correlation or `donkey.last_call`. `is_chat_model=True` is forced. |
| MS Agent Framework | ✅ | ❌ | Static `default_headers` snapshot: no per-run correlation or `donkey.last_call`. |
| Google ADK | ✅ (`extra_headers`) | ❌ | Calls go through ADK's `LiteLlm` model: correlation is per client and `donkey.last_call` is not populated. |
| CrewAI | ✅ (`extra_headers`) | ❌ | Calls go through CrewAI's LiteLLM layer: same behaviour as Google ADK. |

See the [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/develop/docs/verified-apis.md) for how each constructor
signature the adapters depend on is checked.
