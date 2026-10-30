# ADR 0019: A URL read from a configured endpoint follows that endpoint's trust rule, for every credential sent to it

- **Status:** Accepted
- **Date:** 2026-10-30
- **Issue:** #1479 (part of #1414). Extends the endpoint-trust rule that ADR 0005,
  rule 7, keeps, and that #1439 applied to the `donkey.as_user()` token.

## Context

ADR 0005, rule 7, keeps the endpoint-trust rule: `llm_proxy_url` or `base_url`
read from the working directory's `./.donkey-kit.toml` or its `.local` overlay
only receives credentials read from those same files. A standard Anypoint
control-plane host or `DONKEY_TRUST_PROJECT_CONFIG=1` in the environment lifts
it. `DonkeyConfig.check_endpoints` enforces it (`core/config.py`), and
`ARCHITECTURE.md` ("How the pieces connect", the configuration paragraph) lists
where it runs.

The rule covered the two configured endpoints and nothing read from them.
Discovery (`tools/discovery.py`) reads each MCP server's and A2A agent's
gateway URL from the control plane at `base_url` (`resolve_mcp()`,
`resolve_agent()`). It then sends those URLs the service's gateway credential,
which `tools/auth.py` `gateway_auth` builds: the
`llm_proxy_client_id`/`llm_proxy_client_secret` pair in client-id mode, the
`llm_auth` token in the `jwt` and `bearer` modes, or a credential passed to
`donkey.tools.discover(auth=...)`. #1439 (PR #1471) checked the
`donkey.as_user()` token on that path only. `ToolSet._headers()` sent the
gateway credential with no check, and so did `Discovery.agent_card()`, which
`Publication.verify()` and `donkey verify` call.

Picture an untrusted checkout whose `.donkey-kit.toml` names its own `base_url`
and whose `.donkey-kit.local.toml` holds dummy control-plane credentials. That
pairing passes the control-plane check, because both come from the project
files. The control plane it names then decides the gateway URLs, and the SDK
sent them the service's real gateway credential from the environment or the
user file.

## Decision

1. **A URL read from a configured endpoint follows that endpoint's rule.** A
   credential that would not be sent to the endpoint is not sent to a URL the
   SDK read from it either. Today that means the MCP and A2A gateway URLs that
   discovery reads from `base_url`.
2. **The check weighs every credential about to go, in one place.** Before a
   discovered `ToolSet` lists or binds (`ToolSet._headers()`, which every
   binding and `describe()` go through), and before `Discovery.agent_card()`
   reads a card, `tools.auth.check_gateway_endpoint` runs
   `DonkeyConfig.check_endpoints(need="control_plane", ...)` for whatever is
   about to go:
   - the `llm_proxy_client_id`/`llm_proxy_client_secret` pair, weighed by the
     source of each field as the control-plane pair is;
   - the `llm_auth` token in the `jwt` and `bearer` modes, which never comes
     from a file;
   - a `discover(auth=...)` credential, which counts as set in code;
   - inside `donkey.as_user()`, the user token, which counts as set in code.
3. **The refusal is the existing one.** It is a `ConfigError` raised before
   anything is sent. It names each credential and its source, the file and the
   host, and the ways out (`_binding_error`). The overlay is offered only when
   every credential named comes from a file.
4. **The rule's own terms are unchanged.** A standard Anypoint host, a
   `base_url` set in the environment or in code, the credential in the same
   files, or `DONKEY_TRUST_PROJECT_CONFIG=1` lets the credential through.
5. **URLs given in code are not derived.** A `ToolSet` built by hand has its
   server URLs in code, so it carries no check.

`tests/unit/test_gateway_credential_endpoint_trust.py` holds rules 2 to 5 and
`tests/unit/test_as_user_endpoint_trust.py` holds the user-token case.

### Alternatives considered

- **A sibling of `core.identity.check_user_token_endpoint` that works out
  the field credentials itself.** Rejected: it would have to repeat the rule's
  source test for the `llm_proxy_client_*` pair outside `check_endpoints`, or
  pass the pair as an opaque code-credential label. A code label always fails
  the check, so it would lose the overlay as a way out for a pair that
  could move there. Instead `check_endpoints` takes a `fields` argument (the
  config fields sent in place of `client_id`/`client_secret`), so the rule
  stays in one function. `core.*` is Provisional (ADR 0006), so the argument
  needs no deprecation.
- **Check once, at discovery time.** Rejected: an `as_user()` block, a
  `filter()` view or a later bind changes what is sent. The check has to run
  where the headers are built.
- **Leave the gateway credential to the redirect guard (#1415).** Rejected:
  that guard keeps a credential on the server's own origin. Here the server's
  own origin is the untrusted one.

## Consequences

- A service with a non-standard `base_url` in `./.donkey-kit.toml` and its
  gateway credential in the environment or the user file now gets
  `ConfigError` from discovered tool sets, `agent_card()`, `Publication.verify()`
  and `donkey verify`. The exit code is unchanged, because it is a config error.
  The fix is to set `ANYPOINT_BASE_URL` in the environment or code, move the
  pair into `.donkey-kit.local.toml`, or set `DONKEY_TRUST_PROJECT_CONFIG=1`.
- `DonkeyConfig.check_endpoints` gains the keyword-only `fields` argument.
  `core.identity` exports `USER_TOKEN_LABEL`, so both checks name the user token
  the same way.
- `ARCHITECTURE.md` (the configuration paragraph) and
  `website/content/reference/configuration.mdx` ("Which credentials a URL
  receives") list the derived URLs and the credentials checked for them.
  `tool-access/binding.mdx` and `publishing.mdx` say when binding and `verify`
  refuse.
- A future endpoint read from a configured one, such as an A2A client target
  that discovery resolves, follows rule 1. It gets its check where its headers
  are built.
