# CLI Design And Testing Guidance

Maintainer checklist for extending `donkey`, researched on 2026-11-14.
This complements [the current architecture](cli-architecture.md), not the
consumer tutorial or the issue-owned implementation plans. Existing shipped
JSON formats, exit statuses and path semantics are compatibility contracts;
external advice does not authorize breaking them.

## Principles And Sources

| Principle | Application to this CLI | Source |
| --- | --- | --- |
| Results compose; diagnostics explain | TOML/JSON on stdout, errors and next-step hints on stderr; no banners in JSON. | [CLI Guidelines: basics and output](https://clig.dev/#the-basics) |
| Help is part of the interface | Root and command help need no credentials, network, application imports or unrelated extras; include useful examples. | [CLI Guidelines: help](https://clig.dev/#help) |
| Explicit effects | Explain local file writes and remote calls. Read-only preflight still uses the network; dry-run is not inherently offline. | [CLI Guidelines: output](https://clig.dev/#output) |
| Scriptable, predictable invocation | Avoid mandatory prompts. Keep documented global flags before the command rather than rewriting argv. | [Typer callbacks](https://typer.tiangolo.com/tutorial/commands/callback/) |
| Errors are actionable | Name the responsible table/key, explain the failed operation and suggest the next command; do not echo secrets. | [CLI Guidelines: errors](https://clig.dev/#errors) |
| Small commands over shared domain logic | Reuse scanner and planner validation; defer optional execution dependencies rather than build a plugin loader. | [Click: deferring imports](https://click.palletsprojects.com/en/stable/complex/#further-deferring-imports) |
| Tests observe separate channels | Assert stdout, stderr and exit status independently, plus a real installed console-script subprocess. | [Typer testing](https://typer.tiangolo.com/tutorial/testing/) |
| Expected failures are not tracebacks | Handle expected decoding/filesystem/config failures at their boundary; disable traceback locals and do not swallow programming errors. | [Typer exceptions](https://typer.tiangolo.com/tutorial/exceptions/) |

Use Typer's supported APIs and `typer.testing.CliRunner`. Current Typer vendors
Click, so Click's guidance is conceptual evidence, not permission to depend on
external Click internals. See [Typer and Click](https://typer.tiangolo.com/tutorial/click/).
Keep Python 3.10 support and the existing `tomli` fallback:
[`tomllib` arrived in 3.11 and does not write TOML](https://docs.python.org/3.11/library/tomllib.html).

## Deterministic Scaffolding

Only discovered identities and literal source facts belong in a generated
candidate. Never guess a protocol, gateway, publication version, endpoint or
unattributed tool's server. Quote TOML keys and escape string/control characters
correctly; parse the complete candidate before exposing it as writable.

Order output deterministically and omit timestamps, absolute checkout paths and
environment secrets. Preview, JSON and write modes must consume the same
candidate rather than implement three generators. Invalid or ambiguous scan
input must remain an explicit issue, not silently become apparently complete
declarations. A no-op should identify what was preserved.

Connection initialization and declaration scaffolding have different inputs:
runtime configuration may resolve a secret overlay, but static declaration
generation must never import overlay values into output. Tests need independent
sentinels in environment, project, overlay and user configuration. Named-secret
exclusion alone does not protect passwords or tokens embedded in URLs.

## File Safety Is More Than Atomic Rename

[Python `os.replace`](https://docs.python.org/3.10/library/os.html#os.replace)
provides atomic replacement on success and may fail across filesystems. It
silently replaces an existing file. A same-directory staged write avoids
partial replacement, but is **not** a compare-and-swap operation.

- Validate the existing document and the entire proposed result before writing.
- Preserve authored values; define policy for inline tables, dotted keys,
  partial tables, comments, unknown keys and ordering explicitly.
- Refuse directories, symlinks (including dangling links) and forbidden secret
  destinations. Check ancestor-path policy, not just the final filename.
- Use no-clobber creation for an absent destination. An `exists()` check alone
  does not prevent a competing writer from creating it next.
- For updates, state precisely which concurrent modifications are detected.
  A pre-replace content check still has a race; cooperative locking does not
  protect against arbitrary external editors. Do not advertise stronger safety
  than the primitive and tests establish.
- Clean up staged files on handled errors or interruption, preserve relevant
  permissions, and report no success until publication completes.
- Treat crash durability separately from atomic visibility;
  [flush/fsync](https://docs.python.org/3.10/library/os.html#os.fsync) and directory
  metadata guarantees depend on the platform/filesystem.

These are implementation acceptance questions for the writer issues, not a
requirement to add a reusable transaction framework or a TOML-writing dependency
to framework-free `core`.

## Workflow-Scoped Readiness

Report scan validity, offline publication-declaration completeness and
per-service exposure-declaration completeness separately. Include relevant
missing fields and unresolved source facts. An incomplete deployment declaration
does not make valid source or TOML invalid.

Connection credentials and endpoint trust are prerequisites for the relevant
live commands, not evidence that offline scaffolding failed. A clean scan cannot
prove model routing, connected-app scope, gateway availability or enforcement.
Live diagnostics must likewise avoid promoting an unknown failure to accepted
credentials/model; preserve `not checked` when the evidence cannot support a
stronger verdict. Define readiness exit semantics in its issue before coding.

## Regression Matrix

| Surface | Minimum evidence |
| --- | --- |
| Help/version | Root and each command under minimal extras, no credentials, no network and no user imports; documented option placement and unsupported-option exit. |
| Serialization | Quotes, backslashes, newline/tab/CR/control characters; valid complete TOML; deterministic order; no inferred deployment facts. |
| Scanning | Literal and unresolved decorators, lexical shadowing, argument expansion, duplicate identities and runtime-invalid literal values; no application side effects. |
| Path selection | Directory/file PATH, paths with spaces, custom output and non-CWD roots; absent, unreadable, malformed and invalid-UTF-8 config. |
| Writer | Missing/existing/inline/dotted/partial TOML, unknown authored fields, two no-op reruns, directory/symlink destinations, concurrent changes and injected write/replace failure. |
| Secrets | Sentinels in all config sources and URL userinfo/query parameters, including malformed URLs; absent from generated files and both output streams. |
| JSON/terminal | Exactly the documented JSON or documented no-document error path; no ANSI/progress/prompt pollution when redirected or `NO_COLOR` is set. |
| Readiness | Separate workflow states, per-service missing fields, no unrelated credential requirement and no network; unknown facts remain unknown. |
| Doctor | Routing refusals, generic errors, auth/network failures and supported route fallback; observed evidence matches verdict and exit status. |
| Installed package | Console script outside the checkout with minimal supported extras, generated files, JSON, reruns and the onboarding walkthrough. |

Use isolated files and fake transports for SDK tests; published-package acceptance
is a separate release gate. Existing CLI/scanner/planner tests are the starting
point, not proof of uncovered edge cases. Keep runner invocations out of
concurrent threads because they mutate interpreter state; see
[Click testing limitations](https://click.palletsprojects.com/en/stable/testing/).

## Documentation Roles

[Diataxis](https://diataxis.fr/) separates tutorial, how-to, reference and
explanation. Apply that distinction without multiplying pages unnecessarily:

- `website/content/cli.mdx`: consumer CLI page and generated command
  reference. Its "From code to a publish plan" section (#1751) is the
  onboarding tutorial: `init`, `scan`, `scaffold --dry-run`, `scaffold --set`,
  readiness, remediation and `publish --dry-run` offline, then the live gate.
  Its example output comes from a clean install outside the checkout, and the
  sibling acceptance harness runs the same journey against the published
  package.
- `website/content/quickstart.mdx`: short entry path; it links to that
  walkthrough.
- `docs/cli-architecture.md`: contributor explanation of modules, dependencies,
  configuration and execution boundaries.
- This file: sourced design guidance and the reusable regression checklist.
- GitHub issues: concrete design choices, findings, dependencies and acceptance
  criteria. ADRs: changes to architectural invariants or dependency policy.

Never document a planned command as available or an offline check as live
verification. Regenerate generated documentation with its existing tooling and
reconcile the published-package acceptance harness before declaring onboarding
complete.
