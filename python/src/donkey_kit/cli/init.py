"""``donkey init``: bootstrap a commented ``.donkey-kit.toml`` (config resolution).

What ``init`` writes, and where (#1746):

- **Input.** The connection config resolved from the working directory, by the
  same precedence as the SDK (``DonkeyConfig.from_env()``: environment
  variables, ``./.donkey-kit.local.toml``, ``./.donkey-kit.toml``, the user
  file, defaults), plus the global ``--env``. The global ``--config`` names
  only the destination; it is not read as input. Reading the secret overlay is
  fine here, because only the explicitly non-secret fields below are emitted.
- **Output.** ``--config``, else ``./.donkey-kit.toml``. Missing parent
  directories are created. An existing file is a successful no-op unless
  ``--force`` is passed, and ``--force`` regenerates the *whole* file: it is not
  a merge, so declaration tables the old file held (``[donkey.agents]``,
  ``[donkey.publication]``, ...) are not carried over, and init says which.
- **Refusals** (exit 1, nothing written, no JSON document): a destination that
  is a symlink (dangling included), a directory or a secret holder
  (``cli/_files.py``); an emitted URL carrying a credential, or one that cannot
  be parsed, never echoed; a value that cannot be written as UTF-8 TOML.
  ``--force`` relaxes none of these.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

import typer

from ..core.config import (
    LOCAL_TOML_NAME,
    TOKEN_AUTH_MODES,
    TOML_NAME,
    TRUST_PROJECT_CONFIG_ENV,
    DonkeyConfig,
)
from ..core.endpoints import ALLOW_HTTP_ENV, url_credential_problem
from . import _files
from ._app import app

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

__all__ = ["init"]


# Config fields written to .donkey-kit.toml, grouped with a header comment.
# SECRETS ARE DELIBERATELY ABSENT — see _SECRET_FIELDS. This list is
# non-secret connection config only: identifiers, URLs, header names and the
# auth-mode selector. Every name is a DonkeyConfig field (core/config.py _FIELDS).
_INIT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Anypoint control plane (registry)",
        ("client_id", "org_id", "environment", "region", "base_url"),
    ),
    (
        "LLM proxy (data plane) — a SEPARATE credential from the control plane",
        (
            "llm_proxy_url",
            "llm_proxy_auth",
            "llm_proxy_client_id",
            "llm_proxy_wallet_client_id",
            "llm_proxy_key_header",
        ),
    ),
    (
        "Attribution",
        ("application_name", "business_group"),
    ),
)

# Written only when set somewhere, rather than whenever not None, so a default
# `donkey init` writes what it always wrote (#1746).
_ONLY_WHEN_SET = frozenset({"llm_proxy_auth"})

# The fields whose value is a URL, checked for embedded credentials before render.
_URL_FIELDS = frozenset({"base_url", "llm_proxy_url"})

# Written NEVER — as commented placeholders only. A committed config file is
# the wrong home for a secret (config resolution); these belong in a gitignored
# .donkey-kit.local.toml (which the SDK overlays on this file) or in env vars.
_SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("client_secret", "ANYPOINT_CLIENT_SECRET"),
    ("llm_proxy_client_secret", "DONKEY_LLM_PROXY_CLIENT_SECRET"),
    ("llm_proxy_key", "DONKEY_LLM_PROXY_KEY"),
)

# TOML basic-string escapes; every other control character becomes \\uXXXX.
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class _Refused(Exception):
    """init will not write; the message is safe to print (it holds no value)."""


def _toml_str(value: object) -> str:
    """Render ``value`` as a TOML basic string, escaping every character TOML
    requires escaped. Only strings are rendered: a future non-string field needs
    its own TOML type, not ``str()``."""
    if not isinstance(value, str):
        raise TypeError(f"init renders only string fields, not {type(value).__name__}")
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _collect_missing(config: DonkeyConfig) -> list[str]:
    """Every missing required field across BOTH capabilities, in one list —
    reusing ``DonkeyConfig.missing_fields`` (what ``validated`` reports) as the
    single source of truth for what is required (config resolution), rather than
    duplicating the field set here."""
    return [item for need in ("control_plane", "llm") for item in config.missing_fields(need=need)]


def _emitted(config: DonkeyConfig) -> list[tuple[str, list[tuple[str, object]]]]:
    """The groups and the ``(field, value)`` pairs init writes for each."""
    groups = []
    for header, names in _INIT_GROUPS:
        pairs = [
            (name, getattr(config, name))
            for name in names
            if getattr(config, name) is not None
            and (name not in _ONLY_WHEN_SET or config.source_of(name).kind != "default")
        ]
        if pairs:
            groups.append((header, pairs))
    return groups


def _check_urls(config: DonkeyConfig, groups: list[tuple[str, list[tuple[str, object]]]]) -> None:
    for _, pairs in groups:
        for name, value in pairs:
            if name in _URL_FIELDS and isinstance(value, str):
                problem = url_credential_problem(value)
                if problem:
                    raise _Refused(
                        f"{name} (from {config.source_of(name)}) {problem}, so it is not "
                        "written to a committed config file. Fix it where it is set, then "
                        "re-run `donkey init`."
                    )


def _render_toml(config: DonkeyConfig, missing: list[str]) -> str:
    """Hand-render a commented ``.donkey-kit.toml`` from resolved, non-secret
    values. No dependency on a TOML *writer*; the reader (`tomllib`) checks it."""
    lines: list[str] = [
        f"# {TOML_NAME} — generated by `donkey init` from your current environment.",
        "# Review before committing. Resolution order at runtime: values set in",
        f"# code → env → {LOCAL_TOML_NAME} → this file → defaults (config resolution).",
        "#",
        f"# SECRETS ARE NEVER WRITTEN HERE. Put them in a gitignored {LOCAL_TOML_NAME}",
        "# next to this file (same [donkey] table), or in environment variables:",
    ]
    for field, envvar in _SECRET_FIELDS:
        lines.append(f"#   - {field:<24} → env {envvar}")
    lines += [
        "#",
        "# A base_url or llm_proxy_url set here (localhost included) only receives",
        f"# credentials from this file or {LOCAL_TOML_NAME}. If your credentials are",
        "# in environment variables, set the URL there too (ANYPOINT_BASE_URL /",
        f"# DONKEY_LLM_PROXY_URL), or set {TRUST_PROJECT_CONFIG_ENV}=1 to trust this",
        "# directory's config files.",
        "#",
        "# URLs must use https://. Plain http:// is accepted for localhost; for other",
        f"# hosts only with {ALLOW_HTTP_ENV}=1 in the environment (not in this file).",
    ]
    if missing:
        lines += [
            "#",
            "# Still missing (set these before the SDK can reach the platform):",
        ]
        lines += [f"#   - {item}" for item in missing]
    lines += ["", "[donkey]"]
    for header, pairs in _emitted(config):
        lines.append(f"# {header}")
        for name, value in pairs:
            if name == "llm_proxy_auth" and value in TOKEN_AUTH_MODES:
                lines += [
                    f"# In {value} mode the rotating token comes from an AuthProvider passed in",
                    "# code (Donkey(llm_auth=...)). It is never a config value, and the donkey",
                    "# CLI has none, so CLI commands that call the proxy cannot use this mode.",
                ]
            lines.append(f"{name} = {_toml_str(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _checked(config: DonkeyConfig, content: str) -> str:
    """``content`` once it is proven to be UTF-8 TOML holding exactly the values
    init meant to write; otherwise :class:`_Refused`, before any write."""
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _Refused(
            "a resolved value is not valid Unicode text (it is not shown), so it cannot be "
            "written as UTF-8. Fix the value where it is set, then re-run `donkey init`."
        ) from exc
    expected = {name: value for _, pairs in _emitted(config) for name, value in pairs}
    try:
        written = tomllib.loads(content).get("donkey")
    except tomllib.TOMLDecodeError as exc:
        raise _Refused("the generated file would not parse as TOML; nothing was written.") from exc
    if written != expected:
        raise _Refused("the generated file would not round-trip its values; nothing was written.")
    return content


def _dropped_tables(path: Path) -> list[str] | None:
    """The tables in ``path`` that a full regeneration does not carry over, or
    ``None`` when the file could not be read as TOML. Names only, never values."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    dropped = [f"[{key}]" for key in data if key != "donkey"]
    donkey = data.get("donkey")
    if isinstance(donkey, dict):
        dropped += [f"[donkey.{key}]" for key, value in donkey.items() if isinstance(value, dict)]
    return sorted(dropped)


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        help="Regenerate the whole file even if it exists. Not a merge: other tables are dropped.",
    ),
) -> None:
    """Bootstrap a commented `.donkey-kit.toml` from the resolved config.

    Writes every non-secret connection value already resolved from the
    environment or the working directory's config files, auth mode included,
    and names every missing required field at once, from the same check the SDK
    runs at startup, so init never disagrees with a live call about what is
    required. Secrets are never written: each one is a commented pointer to its
    environment variable, and a URL that carries a credential is refused. An
    existing file is left untouched unless `--force` is passed, which
    regenerates it in full. Honors the global `--config` (the file to write,
    not read), `--env` and `--json` flags.
    """
    opts = ctx.obj or {}
    target: Path = opts.get("config") or (Path.cwd() / TOML_NAME)
    as_json: bool = bool(opts.get("json"))

    try:
        _files.check_destination(target)
    except _files.UnsafeDestination as exc:
        _fail(str(exc))

    exists = target.exists()
    if exists and not force:
        msg = f"{target} already exists; leaving it untouched. Re-run with --force to regenerate."
        if as_json:
            typer.echo(json.dumps({"path": str(target), "written": False, "missing": []}))
        else:
            typer.secho(msg, fg="yellow")
        return

    config = DonkeyConfig.from_env()
    env_override = opts.get("env")
    if env_override:
        config = config.with_overrides(environment=env_override)

    missing = _collect_missing(config)
    dropped = _dropped_tables(target) if exists else []
    try:
        _check_urls(config, _emitted(config))
        content = _checked(config, _render_toml(config, missing))
        if exists:
            _files.replace(target, content)
        else:
            _files.create_new(target, content)
    except _Refused as exc:
        _fail(f"Refusing to write {target}: {exc}")
    except (_files.UnsafeDestination, _files.WriteConflict) as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"Could not write {target}: {exc.strerror or exc}")

    if dropped is None:
        typer.secho(f"{target} could not be read as TOML; --force replaced it in full.", err=True)
    elif dropped:
        typer.secho(
            f"--force regenerated {target} in full; it no longer has {', '.join(dropped)}.",
            fg="yellow",
            err=True,
        )

    if as_json:
        typer.echo(json.dumps({"path": str(target), "written": True, "missing": missing}))
        return

    typer.secho(f"Wrote {target}.", fg="green")
    if missing:
        typer.echo("\nStill missing (set before the SDK can reach the platform):")
        for item in missing:
            typer.echo(f"  - {item}")
    else:
        typer.echo("All required fields resolved.")


def _fail(message: str) -> NoReturn:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)
