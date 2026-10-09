# ADR 0010: No hidden global side effects; the SDK doesn't install the global OTel provider implicitly

- **Status:** Accepted
- **Date:** 2026-10-03
- **Issue:** #732 (part of #707). Recorded under the ADR process from #731.

## Context

`core/telemetry.py` `configure_otlp_export` runs from every
`core/runtime.py` `Runtime`, so from every `Donkey()` and from the process
default behind the module-level factories (ADR 0003). When telemetry is on,
an OTLP endpoint env var is set (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or
`OTEL_EXPORTER_OTLP_ENDPOINT`) and `[otel]`
is installed, it builds a `TracerProvider` with a `BatchSpanProcessor` and
calls `trace.set_tracer_provider(provider)`, unless the global provider is
already an SDK `TracerProvider`. It does this at most once per process.

OpenTelemetry lets the global provider be set only once. A host application
that sets up its own provider after constructing a `Donkey` is refused, and
its spans and the SDK's go to the SDK's exporter instead. Constructing an
object has changed process-global state the host owns, and nothing in the
call says so. #732 also notes that the unit suite exported real spans to
whatever collector the environment named, which shows the side effect fires
on plain construction.

## Decision

1. **The rule:** constructing `Donkey`, a `Runtime`, or any SDK object must
   not change process-global state that the host application owns unless the
   user opts in. That covers the OpenTelemetry global providers, logging
   handlers and levels (the package root adds only a `NullHandler`, per
   `CONTRIBUTING.md` §3, Logging), `warnings` filters, signal handlers,
   environment variables and `sys.path`. State the SDK owns, such as the
   process-default runtime (ADR 0003) and its `atexit` close, is not covered.
   Neither is a change scoped to one call and undone before it returns: the
   conformance harness attaches a capture handler to the root logger for one
   agent run and removes it, with the old level, in a `finally`
   (`conformance/harness.py`). A search of `src/` for the calls listed above
   finds one implicit global change today: `configure_otlp_export`.
2. **OTel:** the SDK never calls `trace.set_tracer_provider` implicitly.
   When the host has installed a provider, the SDK's spans use it, including a
   provider the host installs after `Donkey()` was constructed. When the host
   hasn't and zero-config export is on, the SDK exports its own spans through
   a DDK-scoped `TracerProvider` that only the SDK's tracer uses. Installing
   that provider as the global one is an explicit opt-in through config. #732
   names the option.
3. **Tests hold it.** Constructing `Donkey` leaves
   `trace.get_tracer_provider()` unchanged unless the user opted in, and a host
   provider configured after `Donkey()` receives the SDK's spans.
4. **A new global side effect needs this ADR's exception process:** a PR that
   adds one names the opt-in that triggers it and documents it on the page for
   that feature. A side effect with no opt-in needs a new ADR.

### Alternatives considered

- **Keep installing the global provider when none is set** (today's
  behaviour). Rejected: the "none is set yet" check runs at construction time,
  and a host that configures OTel later in start-up loses its provider.
- **Make export opt-in only, with no DDK-scoped provider.** Rejected as the
  default: it would remove the zero-config export (`BG §1.6`, #194) for users
  who set only an OTLP endpoint env var. The scoped provider keeps that
  working without touching global state.
- **Document the behaviour and leave it.** Rejected: OTel's set-once rule
  makes the failure silent, so documentation would only help users who
  already know to look for it.

## Consequences

- `website/content/telemetry.mdx` documents which provider the SDK's spans
  use in each case and the opt-in.
- `configure_otlp_export` and the `core/runtime.py` comment that calls it the
  "single funnel" change with #732.
- Open gap, tracked as #954: with the DDK-scoped provider,
  `trace.get_tracer_provider().force_flush()` no longer reaches the SDK's spans,
  and there is no public flush for the provider the SDK owns, which matters to
  a serverless caller that must flush before the process freezes. Until
  #954 adds one, the caller opts in to the global provider or brings its own.
- The unit suite stops sending spans to a collector named in the
  developer's environment.
- A reviewer can reject a new import-time or construction-time global change by
  citing this ADR.
