# Fixture provenance

A `Format=Gemini` native ingress on `model-based-routing` def 1.0.5 / impl 1.0.9 with two routes and a fallback (#1404). Unlike the def 1.0.4 `gemini_inbound/` captures, the routing headers are returned; every path model was served by `gemini`. Pinned by `tests/unit/test_model_routing_1_0_5_contract.py`; see docs/verified-apis.md §2.

- `generate_content.success`: 2026-11-03, ddk-gemini-multi-vendor (instance 21271488, native Gemini ingress, routes gemini + openai, fallback Route A): path model gemini-3.8-flash; captured by Claude Code at the maintainer's request with the shared ddk-test-client pair.
- `generate_content.cross_vendor`: 2026-11-03, ddk-gemini-multi-vendor (instance 21271488, native Gemini ingress, routes gemini + openai, fallback Route A): path model openai/gpt-5-mini (configured second vendor); captured by Claude Code at the maintainer's request with the shared ddk-test-client pair.
- `generate_content.unresolvable`: 2026-11-03, ddk-gemini-multi-vendor (instance 21271488, native Gemini ingress, routes gemini + openai, fallback Route A): unresolvable path model; captured by Claude Code at the maintainer's request with the shared ddk-test-client pair.
- `generate_content.invalid_json`: 2026-11-03, ddk-gemini-multi-vendor (instance 21271488, native Gemini ingress, routes gemini + openai, fallback Route A): truncated (invalid) JSON body; captured by Claude Code at the maintainer's request with the shared ddk-test-client pair.
- `stream_generate_content.success`: 2026-11-03, ddk-gemini-multi-vendor (instance 21271488, native Gemini ingress, routes gemini + openai, fallback Route A): streamGenerateContent?alt=sse; captured by Claude Code at the maintainer's request with the shared ddk-test-client pair.
