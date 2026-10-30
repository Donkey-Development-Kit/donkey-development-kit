# Custom policies

Status: Live. Shipped, and verified against a real governed gateway.

DDK types the refusals the Omni Gateway's own policies produce (see
[Typed refusals](https://docs.donkey-kit.dev/errors.md)). A custom policy, such as a PDK policy your team
wrote, sends its own refusal shape. DDK cannot know that shape, so by default it
reaches your code as `UpstreamRequestError` or the generic `PolicyViolation`.

`classify.register(match=..., raises=...)` maps one such shape to a
`PolicyViolation` subclass that you define, with your own remediation. After
that, the refusal is raised as your type, a [refusal handler](https://docs.donkey-kit.dev/refusal-handlers.md)
for it runs, and the span names it.

```python
from donkey_kit import classify

classify.register(
    match=dict(status=403, error_type="acme_salary_block"),
    raises=SalaryLeakBlocked,
)
```

  Nothing is enforced on the client. The gateway still decides whether a request
  is refused; a registration only names that decision so your code can act on it.

## Define the exception

A registered type is a `PolicyViolation` subclass with its own `remediation`.
The default inherited from `PolicyViolation` does not count: the remediation is
what your caller reads, so each type states its own.

```python
from donkey_kit import PolicyViolation

class SalaryLeakBlocked(PolicyViolation):
    # Required: this type's own remediation.
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."
    # The slug recorded as donkey.policy.type. Optional when the match has
    # error_type or error_code (the slug defaults to that value); required for a
    # match on headers or body only.
    policy = "acme_salary_block"
```

The slug must match `[a-z][a-z0-9_-]{0,63}`, and it cannot be a built-in
`donkey.policy.type` value such as `pii_detected` or `token_budget`. One slug names one
class, so a span's `donkey.policy.type` always points at one type.

A family of custom policies can share a base class, so that one handler covers
all of them (see [Handlers and spans](#handlers-and-spans)):

```python
class AcmePolicyViolation(PolicyViolation):
    remediation = "An Acme data-handling policy refused this request. See go/acme-policies."

class SalaryLeakBlocked(AcmePolicyViolation):
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."
    policy = "acme_salary_block"

class CustomerPIIBlocked(AcmePolicyViolation):
    remediation = "Customer identifiers must be tokenised before this call."
    policy = "acme_customer_pii"
```

## The match

A `match` names the response's `status` and at least one discriminator. Every
condition in it must hold.

| Key | Matches | Value |
| --- | --- | --- |
| `status` | The HTTP status. Required. | An `int` in 400–499, except 401 (always auth) and 429 (always a rate limit or token budget) |
| `error_type` | The nested error `type`, or the string `status` of Gemini's envelope | `str` |
| `error_code` | The nested error `code`, as a string (a numeric code is compared as its string form) | `str` |
| `headers` | Response headers. Names are case-insensitive; values are exact; several are ANDed. | `{name: value}` |
| `body` | Values in the JSON body, by dotted path | `{"a.b": str \| int \| bool \| re.Pattern}` |

Each example below shows the gateway's response, then the registration that
types it.

### Nested `error.type`

The OpenAI-style envelope, the same style the built-in PII policy uses.

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{"error": {"type": "acme_salary_block", "message": "Outbound salary figure detected: 182,000"}}
```

```python
donkey.classify.register(
    match=dict(status=403, error_type="acme_salary_block"),
    raises=SalaryLeakBlocked,
)
```

### Nested `error.type` on Gemini's list envelope

`error_type` also reads Gemini's string `status`. The list envelope is unwrapped
the same way built-in classification unwraps it.

```http
HTTP/1.1 403 Forbidden
content-type: application/json

[{"error": {"code": 403, "status": "ACME_SALARY_BLOCK", "message": "Blocked by policy."}}]
```

```python
class SalaryLeakBlocked(PolicyViolation):
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."
    policy = "acme_salary_block"  # required here: "ACME_SALARY_BLOCK" is not a valid slug

donkey.classify.register(
    match=dict(status=403, error_type="ACME_SALARY_BLOCK"),
    raises=SalaryLeakBlocked,
)
```

### Nested `error.code`

The style the built-in Agent Kill Switch refusal uses.

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{"error": {"code": "acme_salary_block", "message": "Blocked by policy."}}
```

```python
donkey.classify.register(
    match=dict(status=403, error_code="acme_salary_block"),
    raises=SalaryLeakBlocked,
)
```

A numeric code is compared as a string, as built-in classification does, so
`{"error": {"code": 4031}}` is matched by `error_code="4031"`. The value you
register must be a `str`.

### A header marker

The body is ignored.

```http
HTTP/1.1 403 Forbidden
content-type: application/json
x-acme-policy: salary-block

{"message": "nope"}
```

```python
donkey.classify.register(
    match=dict(status=403, headers={"x-acme-policy": "salary-block"}),
    raises=SalaryLeakBlocked,  # must set policy: a header match has no natural slug
)
```

Several headers are ANDed:
`headers={"x-acme-policy": "salary-block", "x-acme-action": "reject"}`.

### A flat-string body, exact

```http
HTTP/1.1 451 Unavailable For Legal Reasons
content-type: application/json

{"error": "Salary figures may not leave HR"}
```

```python
donkey.classify.register(
    match=dict(status=451, body={"error": "Salary figures may not leave HR"}),
    raises=SalaryLeakBlocked,
)
```

A plain `str` is always an exact match, never a substring.

### A flat-string body with dynamic text

Use a compiled pattern when the text varies.

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{"error": "Salary figures for employee 4411 blocked"}
```

```python
import re

donkey.classify.register(
    match=dict(status=403, body={"error": re.compile(r"Salary figures for employee \d+ blocked")}),
    raises=SalaryLeakBlocked,
)
```

The pattern must match the whole value (`fullmatch`), and it applies only to
string values.

### A dotted body path

For a JSON envelope of your own.

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{"policy": {"id": "salary-guard", "action": "reject"}, "detail": "Blocked by policy."}
```

```python
donkey.classify.register(
    match=dict(status=403, body={"policy.id": "salary-guard", "policy.action": "reject"}),
    raises=SalaryLeakBlocked,
)
```

Each segment must name an object key, so lists are not traversed. Comparison is
type-strict: `{"retryable": False}` does not match `"false"`, and `{"rule": 7}`
does not match `"7"`.

### Combining conditions, and specificity

When two registrations match the same response, the one with more conditions
wins.

```http
HTTP/1.1 403 Forbidden
content-type: application/json
x-acme-policy: block

{"error": {"code": "salary", "message": "Blocked by policy."}}
```

```python
class AcmeBlocked(AcmePolicyViolation):
    remediation = "An Acme policy refused this request."
    policy = "acme_generic_block"

# One discriminator: any Acme refusal.
donkey.classify.register(
    match=dict(status=403, headers={"x-acme-policy": "block"}),
    raises=AcmeBlocked,
)
# Two discriminators: the salary refusal specifically. It wins when both match.
donkey.classify.register(
    match=dict(status=403, headers={"x-acme-policy": "block"}, error_code="salary"),
    raises=SalaryLeakBlocked,
)
```

Two registrations that match with the **same** number of conditions are a tie.
A tie is never settled by registration order. The response becomes the generic
`PolicyViolation`, with `policy="unknown"`, and its remediation names both
registrations. DDK logs a warning the first time it sees each tie.

### Built-in shapes always win

The gateway's own policies are classified first, so a registration can never
take a PII, injection, kill-switch, auth, rate-limit or token-budget refusal.
A registration that one of them would claim is rejected when you register it,
naming the rule that claims it (see [Rejected at registration
time](#rejected-at-registration-time)).

## Where and when to register

Registrations are process-wide. Register once, at import time, before the agent
runs. For example, in a `policies.py` your entry point imports:

```python
# acme_agent/policies.py
from donkey_kit import classify
from acme_agent.errors import CustomerPIIBlocked, SalaryLeakBlocked

classify.register(match=dict(status=403, error_code="salary"), raises=SalaryLeakBlocked)
classify.register(match=dict(status=403, error_code="customer_pii"), raises=CustomerPIIBlocked)
```

`donkey_kit.classify` and `donkey.classify` on a `Donkey` are the same object,
so they share one registry. A client created before a registration still sees
it, because every classification reads the current registry.

To inspect or remove registrations:

```python
from donkey_kit import classify

classify.registered()  # the current registrations, oldest first, as frozen PolicyRegistration objects
classify.unregister(SalaryLeakBlocked)  # removes every registration that raises it
```

## What your code gets

Inside `donkey.run()`, or under `@donkey.governed`, the refusal reaches your code
as the registered type, whichever framework made the call. `classify(err.response)`
on a framework's own error returns it too.

```python
try:
    with donkey.run():
        llm.invoke("Summarise Q3 compensation for the HR team")
except SalaryLeakBlocked as e:
    e.policy          # "acme_salary_block"
    e.remediation     # "Salary figures may not leave HR. ..."
    str(e)            # "Request refused by custom policy acme_salary_block (403)."
    e.response        # the raw response, the gateway's own text included
    e.correlation_id, e.call_id, e.request_id
```

  The exception's message never repeats the gateway's text. A custom policy's
  refusal may quote the content it refused (here, the salary figure), and
  exception messages end up in logs. The text stays on `e.response` for code
  that needs it.

## Handlers and spans

A registered type works with [refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md) with no extra
wiring, including handlers on a shared base class:

```python
# One type.
donkey.on(SalaryLeakBlocked).escalate(to=hr_review_queue)

# The whole family, through the shared base.
donkey.on(AcmePolicyViolation).call(lambda ctx: notify_user(ctx.violation.remediation))

# Rewrite and resend: the gateway judges the new request from scratch.
def redact_and_retry(ctx):
    body = ctx.body
    body["messages"][-1]["content"] = redact_salaries(body["messages"][-1]["content"])
    ctx.resend(json=body)

donkey.on(SalaryLeakBlocked).call(redact_and_retry, max_times=1)
```

The span for the refused call records `donkey.policy.decision="refuse"` and the
slug verbatim, `donkey.policy.type="acme_salary_block"`, so a trace and the
gateway's own logs share one string (see [Telemetry](https://docs.donkey-kit.dev/telemetry.md)).

CrewAI is the exception: its native provider owns the transport, so neither
`donkey.run()` nor a handler sees its refusals. The README's exemptions table
records it.

## Testing

`donkey.simulate(SalaryLeakBlocked)` works for a registered type with no
hand-written fixture. It serves the minimal response the registration describes,
through the real transport, and checks that the response is classified back to
your type.

Registrations are process-wide, so a registration made in one test would leak
into the next. The plugin's `donkey` fixture drops any registration made during
the test at teardown, and so does the `donkey_classify_registry` fixture, which
yields the registry itself. Registrations made at import time, by the module
that defines your types, are kept.

```python
import pytest

import acme_agent.policies  # noqa: F401  (registers the custom types)
from acme_agent.errors import SalaryLeakBlocked

def test_salary_block_is_typed(donkey):
    llm = donkey.openai(sync=True)
    with donkey.simulate(SalaryLeakBlocked), pytest.raises(SalaryLeakBlocked) as exc:
        with donkey.run():
            llm.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "Summarise Q3 compensation"}],
            )
    assert exc.value.policy == "acme_salary_block"

def test_a_registration_made_here_does_not_leak(donkey_classify_registry):
    donkey_classify_registry.register(
        match=dict(status=403, headers={"x-acme-policy": "salary-block"}),
        raises=SalaryLeakBlocked,
    )
    ...
```

Outside pytest, `donkey_kit._testing.isolated_classify()` is the same
snapshot-and-restore as a context manager.

The [`gateway` fixture](https://docs.donkey-kit.dev/testing.md#the-gateway-fixture) and `donkey mock` run in a
separate process, which cannot see your process's registry, so they serve only
the built-in refusal shapes.

## Rejected at registration time

`register()` checks every rule when you call it, so a mistake fails at import
time rather than at the first refusal. An unknown key, a value of the wrong type
(an empty string included) or a `raises` that is not registrable raises
`TypeError`; the rest raise `ValueError`.

| Registration | Why it is rejected |
| --- | --- |
| `match=dict(status=403)` | No discriminator, so it would catch every 403 |
| `match=dict(status=403, error_typ="x")` | Unknown key (a typo) |
| `match=dict(status="403", error_type="x")` | `status` must be an `int` |
| `match=dict(status=401, error_type="x")` | 401 is always auth |
| `match=dict(status=429, error_type="x")` | 429 is always a rate limit or token budget |
| `match=dict(status=500, error_type="x")` | Only a 4xx is a refusal |
| `match=dict(status=403, error_type="pii_detected")` | Claimed by the built-in PII rule, so it could never match |
| `match=dict(status=403, error_code="agent_killed")` | Claimed by the built-in kill-switch rule |
| `match=dict(status=403, body={"error": "access_denied"})` | Claimed by the built-in CIBA step-up rule (`GatewayApprovalDenied`) |
| `match=dict(status=403, headers={"x-injection-protection": "blocked"})` | Claimed by the built-in injection-protection rule |
| `match=dict(status=403, headers={"www-authenticate": "Bearer"})` | Claimed by the built-in auth rule |
| `match=dict(status=400, error_type="invalid_request_error")` | Its only discriminator is an upstream provider's error value, so a provider mistake would become a policy refusal |
| `match=dict(status=403, error_type="x", body={"error": "y"})` | Contradictory: `error` cannot be both an object and a string |
| `match=dict(status=403, error_type="")` | An empty value discriminates nothing |
| `match=dict(status=403, body={"error": re.compile(r"(?=a)b")})` | A pattern no string can match |
| The same `match` registered twice | A duplicate |
| `raises` with no `remediation` of its own | The remediation is required (BG §1.2) |
| `raises` subclassing `AgentKilled` or another built-in type | Built-in types are not extensible, and `AgentKilled` is terminal |
| `raises` that is not a `PolicyViolation` subclass | Only policy refusals are registrable |
| `raises` whose constructor cannot take `(message, policy=..., **kw)` | `classify()` could not construct it |
| `raises.policy = "token_budget"`, or any built-in slug | `donkey.policy.type` would be ambiguous |
| A slug already used by another registered class | One slug names one type |
| A `match` on headers or body only, with no `policy` on the class | There is no slug to record |
| A slug that fails the slug pattern, such as `"ACME BLOCK"` | Set a valid `policy` on the class |

## Next steps

- [Typed refusals](https://docs.donkey-kit.dev/errors.md): the built-in refusal types and how `classify()` tells them apart.
- [Refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md): escalate, notify or rewrite and resend on a refusal.
- [Testing & conformance](https://docs.donkey-kit.dev/testing.md): `simulate()`, the conformance plugin and its fixtures.
