# ADR 0005: Config precedence is kwargs, env, local file, project file, user file, default, resolved per field from one table

- **Status:** Proposed
- **Date:** 2026-10-03
- **Issue:** #727 (part of #707). Recorded under the ADR process from #731.

## Context

The build plan's config standing invariant (`§2.1`) says configuration
resolves kwargs → env vars → `.donkey-kit.toml` → default, reports every
missing field at once, and has `Donkey.from_env()` as its entry point. The code
does part of this (`core/config.py`, and `ARCHITECTURE.md`, "How the pieces
connect", the configuration paragraph):

- `DonkeyConfig.from_env()` takes no arguments. `Donkey.from_env()` accepts
  only the cost tags (`team`, `project`, `env`, `enduser_id`) and
  `on_model_substitution`. A value set in code is one changed afterwards with
  `with_overrides(...)` or `dataclasses.replace(...)`.
- `DonkeyConfig(...)` built directly reads neither the environment nor the
  files. `Donkey(config)` uses it as is.
- `core/config.py` `_load_config_files` merges the working directory's
  `.donkey-kit.local.toml` over its `.donkey-kit.toml` key by key (#831). The
  user file (`$XDG_CONFIG_HOME/.donkey-kit.toml`, or
  `~/.config/.donkey-kit.toml`) is read only when neither exists, so it never
  fills in a field the project files leave unset.
- Each field records where it came from (`DonkeyConfig.source_of`), and
  `DonkeyConfig.check_endpoints` uses that so an endpoint read from the
  working directory's files only receives credentials from those files.
- The env var for each field is named once, in `core/config.py`, but its TOML
  key, parser and validator are spread through the module.

## Decision

1. **Precedence, per field, highest first:** a keyword argument set in code →
   the environment variable → `./.donkey-kit.local.toml` →
   `./.donkey-kit.toml` → the user file (`$XDG_CONFIG_HOME/.donkey-kit.toml`,
   or `~/.config/.donkey-kit.toml` when that variable is unset, empty or relative, as
   `core/config.py` `_user_config_file` does today) → the
   field's default. The files are merged, not chosen: a field that no
   higher layer sets is filled from the next one down. Nested tables merge
   recursively; scalars and arrays replace, as #831 already does for the two
   project files.
2. **One declarative field table** in `core/config.py` lists, for each field,
   its env var, its TOML key, its parser and its validator. Resolution,
   provenance (`source_of`) and the precedence section of the configuration
   docs are derived from the table, or tested against it.
3. **`DonkeyConfig.resolve(*, path=None, **overrides)` is the layered
   constructor.** `DonkeyConfig.from_env()` is `resolve()` with no overrides.
   `Donkey.from_env(**overrides)` forwards to it and accepts every
   `DonkeyConfig` field, not only the cost tags.
4. **Missing fields are reported at once**, in one `ConfigError`, as `§2.1`
   requires and the code does today.
5. **`with_overrides(...)` re-validates** the new instance, so an override
   can't produce a config that `resolve()` would have refused.
6. **A directly built `DonkeyConfig(...)` stays a plain value** (ADR 0001): it
   reads neither the environment nor the files. Code that wants the layers
   calls `resolve()` or `from_env()`.
7. **Provenance and the endpoint-trust rule are unchanged.** A value from the
   working directory's files is still trusted only with credentials from those
   files (`DonkeyConfig.check_endpoints`), and the user file counts as outside
   the working directory, as it does today.

### Alternatives considered

- **Keep "first file found wins".** Rejected: a developer with a user file
  and a project file can't tell which one applied, and the
  `.donkey-kit.local.toml` that `donkey init` recommends must overlay the
  project file to be useful (#831 fixed that pair, not the user file).
- **Make `DonkeyConfig(...)` read the environment.** Rejected: it would turn a
  frozen value object into an I/O call, and tests and callers that build a
  config by hand would start picking up the developer's shell.
- **Put the file layers above the environment.** Rejected: twelve-factor
  deployments set the environment per process, and an env var must be able to
  override a committed file without editing it.

## Consequences

- Precedence tests cover each layer, including the user file filling a field
  the project files leave unset (#727).
- `ARCHITECTURE.md`'s configuration paragraph and
  `website/content/reference/configuration.mdx` describe the new order, and
  the latter is generated from or tested against the field table.
- The build plan's `§2.1` wording ("kwargs → env vars → `.donkey-kit.toml` →
  default") is left as written: the decision refines its file layer into three
  files and keeps its order.
- `donkey --config <path>` (deferred from #811) can use `resolve(path=...)`
  once it exists.
