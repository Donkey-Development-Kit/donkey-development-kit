# Fixture provenance

The `model-based-routing` 400 for a `provider/model` whose provider has no route on the proxy and no fallback is configured (#1740). The gateway answers before any upstream call, with a flat-string `error`. Pinned by `tests/unit/test_llm_proxy_contract.py`; see docs/verified-apis.md §2 "Model routing" and §4.

- `chat_completions.unsupported_provider_400`: 2026-11-14, a UI-created `Format=OpenAI` Model Proxy (model-based-routing def 1.0.5 / impl 1.0.10, v2 outbound chain, routes openai + gemini + anthropic + azureopenai, no fallback): `mistral/mistral-small`; captured by Claude Code at the maintainer's request. The status line was normalised to `HTTP/1.1 400 Bad Request`.
- `messages.unsupported_provider_400`: 2026-11-14, a UI-created native `Format=Anthropic` Model Proxy (model-based-routing impl 1.0.10, one anthropic route, no fallback): `openai/gpt-5-mini` on `POST /v1/messages`; captured by Claude Code at the maintainer's request. Status line normalised as above.
