# ADR 0002: One framework-agnostic typed-refusal bridge, in core

- **Status:** Accepted
- **Amended by:** #950 (2026-10-05), exception groups.
- **Date:** 2026-10-03
- **Issue:** #724 (part of #707). Recorded under the ADR process from #731.

## Context

`ARCHITECTURE.md` ("Error-taxonomy design", invariant 1) says a policy refusal
is never retried and is distinguishable from a transient error at the
framework boundary. Today that holds only where the user wraps each call site
by hand, and only one wrapper exists:

- `typed_refusals()` lives in `integrations/langgraph.py`. It needs nothing
  from LangGraph: it catches `openai.APIStatusError`, runs
  `core/errors.py` `classify()` on `exc.response`, and raises the typed error
  `from None` with the original on `framework_error`. It deliberately lets
  `APIConnectionError` and `APITimeoutError` through.
- The transport raises some errors typed inside `send()` (for example
  `ModelSubstituted` with `on_model_substitution="raise"`, and
  `GatewayUnavailable`). Every provider SDK above it re-wraps them, so through
  `donkey.openai()` they arrive as `openai.APIConnectionError` with the typed
  error only on `__cause__`. A simulated `PIIDetected` arrives as
  `openai.PermissionDeniedError` (evidence in #724).
- `Donkey.governed()` says its scope carries "typed refusals", but
  `RunScope.__exit__`/`__aexit__` in `core/telemetry.py` only unbind the run
  and translate nothing.
- The transport's `_on_refusal` hook has no caller (`ARCHITECTURE.md` hook
  table).
- Other frameworks wrap errors in their own types (Strands turns a 429 into
  `ModelThrottledException`; CrewAI and LiteLLM wrap provider errors), and the
  docs tell users to hand-roll `classify()` (`website/content/errors.mdx`).

## Decision

1. **The bridge lives in core** as `core/refusals.py` with
   `translate(exc) -> DonkeyError | None`. It walks `__cause__` and
   `__context__` looking for a `DonkeyError` and returns the first one. Failing
   that, it classifies a status error by duck type: an `exc.response` that has
   a `status_code` and `headers` goes through `classify()`. This covers the
   openai, anthropic and google-genai error types without core importing any of
   them, so the framework-free core rule (`§1.1`) holds.
2. **Anything else passes through unchanged.** `translate()` returns `None`
   for an exception that is neither a `DonkeyError` nor carries one nor has a
   classifiable response, and the bridge re-raises the original. A plain
   connection error with no `DonkeyError` in its chain stays what it was.
3. **Framework wrappers are unwrapped by adapter-declared translators.** An
   adapter whose framework wraps errors in its own types declares an optional
   translator for them on its roster entry (`AdapterSpec`, ADR 0004). No
   adapter module carries its own classification logic.
4. **One public entry point:** `donkey_kit.typed_refusals()`, usable as a
   sync or async context manager and as a decorator. The typed error is raised
   `from None`, with the original on `framework_error`, as the LangGraph helper
   does today, so a PII block's rejected text isn't rendered twice in a
   traceback.
5. **`donkey.run()` and `@governed` either apply the bridge on exit or stop
   promising it.** #724 picks one: apply it by default, or correct the
   docstrings and make it opt-in (`governed(typed=True)`). Either way the
   docstring and the behaviour agree.
6. **A classified status error must have come through the SDK's own
   transport.** `translate()` runs `classify()` on an `exc.response` only when
   that response's request carries `extensions['donkey_correlation_header']`
   or `extensions['donkey_call_id_header']` (`core/refusals.py`
   `_SENT_BY_TRANSPORT`), which the transport stamps when it sends. A status
   error from some other HTTP call in the same block is not a governed refusal
   and passes through. The correlation stamp is set on every client the
   transport builds, control-plane clients included
   (`core/transport/headers.py`: only the attribution and cost headers are skipped on
   a `control_plane` client). So an `httpx.HTTPStatusError` from a
   donkey-transport control-plane or MCP call inside `donkey.run()` is
   classified too. That is intended for gateway-fronted MCP, where the
   gateway's refusal should surface as its typed class; it is not limited to
   model calls.
7. **`integrations.langgraph.typed_refusals` is rebuilt on the bridge**, and
   each adapter exposes the same helper.

### Alternatives considered

- **Keep one bridge per adapter.** Rejected: the LangGraph helper already
  shows the logic is framework-free, and copying it into seven more adapters
  is seven chances to drift.
- **Import each provider SDK's error classes in the bridge.** Rejected: core
  may not import a framework, and a duck-typed `exc.response` reaches the same
  information.
- **Wire `_on_refusal` and translate in the transport.** Rejected as the whole
  answer: the transport already raises typed errors, and the problem is the
  SDK layer above it re-wrapping them. The bridge has to sit where the user's
  code catches the error. `_on_refusal` stays a separate question (#728, #208).

## Consequences

- Conformance scenarios for the raw openai client, anthropic and LangGraph
  assert that `GatewayUnavailable`, `ModelSubstituted` and `PIIDetected` reach
  user code as their typed class inside `donkey.run()` and `@governed` (#724).
- `website/content/errors.mdx` and the shared adapter error note replace the
  hand-rolled `classify()` pattern with `typed_refusals()`.
- `typed_refusals` joins `donkey_kit.__all__`, which ADR 0006 governs.
- Declarative refusal handlers (#208) can attach to the bridge rather than to a
  transport hook.
- Follow-ups outside #724: unwrapping an `ExceptionGroup` (a refusal raised
  inside a task group reaches the bridge wrapped) is #950, decided in the
  amendment below; Strands retries a
  429 before the bridge sees it (#951); bridge conformance for openai-agents,
  LlamaIndex and the ADK gemini path is #955.

## Amendment (2026-10-05, #950)

A refusal raised inside an `asyncio.TaskGroup` (or an anyio task group)
leaves the block wrapped in an `ExceptionGroup`, and the chain walk in
decision 1 never looked inside one. The bridge now does, by this rule:

1. **Each leaf is translated on its own.** The group is flattened depth first,
   and every leaf that is an `Exception` goes through the same chain walk and
   adapter translators as a lone exception. A `KeyboardInterrupt`,
   `SystemExit` or cancellation leaf is never translated, matching the
   non-group rule.
2. **A uniform group collapses.** When every leaf translates to the same
   `DonkeyError` class, the bridge raises that typed error in the group's
   place: the first leaf's, depth first, as the same object `translate()`
   returns for that leaf. So `except PIIDetected` catches a refusal from a
   task group exactly as it does without one. `translate(group)` returns that
   typed error, and `None` for any other group. The other leaves, all of the
   same refusal class, are dropped. A leaf that was already a `DonkeyError` also counts, so a
   `PIIDetected` raised directly in a task comes out typed rather than grouped.
3. **A mixed group keeps its shape.** When the leaves are of different
   classes, or any leaf is not a refusal, the bridge raises a rebuilt group:
   the same nesting and messages, built with `derive()` like
   `BaseExceptionGroup.split()`, with each translatable leaf replaced by its
   typed error. `except* PIIDetected` then matches. A leaf the bridge cannot
   type, and a subgroup with nothing to replace, is kept as the same object. A
   `BaseExceptionGroup` stays a `BaseExceptionGroup`.
4. **A group with nothing to translate is not touched.** It propagates as the
   same object, as a non-refusal does under decision 2.
5. **Chaining follows decision 4.** A collapsed error is raised as a lone
   typed error is, `from` its own `__cause__`, with the leaf's framework error
   on `framework_error`. A rebuilt group keeps the original group's
   `__cause__`, `__traceback__` and `__notes__` and is raised with its context
   suppressed, so the original group, whose leaves' messages can repeat the
   blocked values, is not rendered. Each typed leaf carries its own framework
   error on `framework_error`.
6. **Python 3.10 needs no new dependency.** `BaseExceptionGroup` is builtin
   from 3.11. Below it, the bridge also recognises the `exceptiongroup`
   backport's class, which anyio raises there, once something else has
   imported it. Core never imports the backport, so the httpx-and-stdlib rule
   (`§1.1`) holds.

Alternatives considered: always keeping the group, even a uniform one, would
make `except PIIDetected` miss every task-group refusal. Raising the first
refusal of a mixed group would hide the user's own bugs in sibling tasks.
