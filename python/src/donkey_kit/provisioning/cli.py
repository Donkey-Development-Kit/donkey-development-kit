"""The ``donkey`` CLI (provisioning-as-code, BG §2.5).

Telemetry is on by default (BG §1.6), but export stays inert unless an
OTLP endpoint is configured: set ``OTEL_EXPORTER_OTLP_ENDPOINT`` and spans flow
to your own sink with no SDK-specific env var; opt out entirely with
``DONKEY_TELEMETRY=false``. Commands that need a verified
platform API print an honest, actionable "blocked pending verification" message
and exit non-zero rather than fabricating calls (working instruction #2).
Commands that need no platform API (spec validation) do real work now.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

try:
    import typer
except ImportError:  # pragma: no cover - install-time guidance
    print(
        'The CLI needs the [cli] extra. Install it with:\n'
        '    pip install "donkey-kit[cli]"',
        file=sys.stderr,
    )
    raise SystemExit(1) from None

from ..core.config import _TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError, DonkeyError
from .spec import DonkeySpec

app = typer.Typer(
    add_completion=False,
    help="SDK for Agent Fabric — governed models, tools, provisioning-as-code.",
)


@app.callback()
def _global(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", metavar="PATH", help=f"Path to {_TOML_NAME} (default: cwd)."
    ),
    env: str | None = typer.Option(
        None, "--env", metavar="NAME", help="Anypoint environment override (e.g. Sandbox)."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON where the command supports it."
    ),
) -> None:
    """Global flags shared by every command (provisioning-as-code). Precede the subcommand:
    ``donkey --json init``, ``donkey --config ./cfg.toml doctor``."""
    ctx.obj = {"config": config, "env": env, "json": as_json}


def _load_spec(file: Path) -> DonkeySpec:
    try:
        return DonkeySpec.from_yaml(file.read_text())
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        typer.secho(f"Invalid spec {file}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


def _blocked(what: str) -> None:
    typer.secho(f"blocked on verification: {what}", fg="yellow", err=True)
    typer.secho(
        "See docs/verified-apis.md — this command is scaffolded but not wired until the underlying "
        "platform API is confirmed against a sandbox (verification discipline).",
        err=True,
    )
    raise typer.Exit(3)


@app.command(hidden=True)
def validate(file: Path = typer.Option(..., "-f", "--file", help="donkey.yaml")) -> None:
    """Validate a donkey.yaml against the schema (needs no platform API).

    Hidden: it operates on the ``donkey.yaml`` provisioning spec, part of the
    provisioning control-plane surface excluded by the build plan's "Do not build,
    at any phase" boundary. The command still works for anyone driving that YAML,
    but the four supported ``donkey`` commands are
    ``init``/``doctor``/``mock``/``test``.
    """
    spec = _load_spec(file)
    typer.secho(
        f"OK: {spec.metadata.name} — {len(spec.mcpBridges)} MCP bridge(s), "
        f"env {spec.metadata.environment}.",
        fg="green",
    )


@app.command(hidden=True)
def plan(file: Path = typer.Option(..., "-f", "--file"),
         dry_run: bool = typer.Option(False, "--dry-run"),
         out: Path | None = typer.Option(None, "--out", help="write plan.json for CI")) -> None:
    """Show the create/update/remove plan (read-before-write, provisioning-as-code)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning read API (provisioning-as-code)")


@app.command(hidden=True)
def apply(file: Path = typer.Option(..., "-f", "--file"),
          auto_approve: bool = typer.Option(False, "--auto-approve")) -> None:
    """Apply the plan (CI-only, platform-controlled creds, provisioning-as-code)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning write API (provisioning-as-code)")


@app.command(hidden=True)
def drift(file: Path = typer.Option(..., "-f", "--file")) -> None:
    """Compare live state against the spec; exit non-zero on drift (provisioning-as-code)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning read API (provisioning-as-code)")


@app.command(hidden=True)
def lint(file: Path = typer.Option(..., "-f", "--file")) -> None:
    """Governance lint (provisioning-as-code). Local spec-shape checks run now; ruleset
    resolution is gated (verification discipline)."""
    _load_spec(file)
    _blocked("governance rulesets resolution API (provisioning-as-code)")


@app.command(hidden=True)
def generate(file: Path = typer.Option(..., "-f", "--file"),
             target: str = typer.Option("terraform", "--target")) -> None:
    """Emit Terraform from the spec — the provisioning-as-code pivot if provisioning is UI-only."""
    _load_spec(file)
    _blocked("Terraform provider coverage enumeration (provisioning-as-code)")


@app.command(hidden=True)
def status() -> None:
    """Render published / reachable / governed per asset (BG §2.5)."""
    _blocked("Exchange + API Manager read APIs (BG §2.5)")


# Config fields written to .donkey-kit.toml, grouped with a header comment.
# SECRETS ARE DELIBERATELY ABSENT — see _SECRET_FIELDS. This list is
# non-secret connection config only.
_INIT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Anypoint control plane (registry + provisioning)",
        ("client_id", "org_id", "environment", "region", "base_url"),
    ),
    (
        "LLM proxy (data plane) — a SEPARATE credential from the control plane",
        ("llm_proxy_url", "llm_proxy_client_id"),
    ),
    (
        "Attribution",
        ("application_name", "business_group"),
    ),
)

# Written NEVER — as commented placeholders only. A committed config file is
# the wrong home for a secret (config resolution); these belong in env vars or a gitignored
# .donkey-kit.local.toml.
_SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("client_secret", "ANYPOINT_CLIENT_SECRET"),
    ("llm_proxy_client_secret", "DONKEY_LLM_PROXY_CLIENT_SECRET"),
    ("llm_proxy_key", "DONKEY_LLM_PROXY_KEY"),
)


def _toml_str(value: str) -> str:
    """Render a TOML basic string, escaping the two characters that matter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _collect_missing(config: DonkeyConfig) -> list[str]:
    """Every missing required field across BOTH capabilities, in one list —
    reusing ``DonkeyConfig.validated`` as the single source of truth for what
    is required (config resolution), rather than duplicating the field set here."""
    missing: list[str] = []
    for need in ("control_plane", "llm"):
        try:
            config.validated(need=need)
        except ConfigError as exc:
            for line in str(exc).splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    missing.append(stripped[2:])
    return missing


def _render_toml(config: DonkeyConfig, missing: list[str]) -> str:
    """Hand-render a commented ``.donkey-kit.toml`` from resolved, non-secret
    values. No dependency on a TOML *writer*; the reader (`tomllib`) is enough."""
    lines: list[str] = [
        f"# {_TOML_NAME} — generated by `donkey init` from your current environment.",
        "# Review before committing. Resolution order at runtime: kwargs → env →",
        "# this file → defaults (config resolution).",
        "#",
        "# SECRETS ARE NEVER WRITTEN HERE. Provide them via environment variables",
        "# (or a gitignored .donkey-kit.local.toml):",
    ]
    for field, envvar in _SECRET_FIELDS:
        lines.append(f"#   - {field:<24} → env {envvar}")
    if missing:
        lines += [
            "#",
            "# Still missing (set these before the SDK can reach the platform):",
        ]
        lines += [f"#   - {item}" for item in missing]
    lines += ["", "[donkey]"]
    for header, group_fields in _INIT_GROUPS:
        emitted = [
            (name, getattr(config, name))
            for name in group_fields
            if getattr(config, name) is not None
        ]
        if not emitted:
            continue
        lines.append(f"# {header}")
        for name, value in emitted:
            lines.append(f"{name} = {_toml_str(str(value))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(
        False, "--force", help="Regenerate even if the file already exists."
    ),
) -> None:
    """Bootstrap a commented ``.donkey-kit.toml`` from the resolved config (config resolution).

    Writes every non-secret value already visible via kwargs/env/toml, and names
    EVERY missing required field at once — reusing the same
    ``DonkeyConfig.validated`` report the SDK uses at runtime, so ``init`` never
    disagrees with a live call about what is required. Secrets are never written
    (they surface as commented ``env`` pointers). Idempotent: an existing file is
    left untouched unless ``--force`` is passed. Honors the global ``--config``
    (write target), ``--env`` (environment override), and ``--json`` flags.
    """
    opts = ctx.obj or {}
    target: Path = opts.get("config") or (Path.cwd() / _TOML_NAME)
    as_json: bool = bool(opts.get("json"))

    if target.exists() and not force:
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
    content = _render_toml(config, missing)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    except OSError as exc:
        typer.secho(f"Could not write {target}: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

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


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def test(
    ctx: typer.Context,
    agent: str | None = typer.Option(
        None,
        "--agent",
        metavar="MODULE:FACTORY",
        help="Import path to your agent factory, e.g. my.pkg:make_agent.",
    ),
) -> None:
    """Run the conformance suite against your agent (BG §1.5).

    A thin front end to ``pytest --donkey-conformance`` — it does not
    re-implement the runner. Your ``--agent MODULE:FACTORY`` and any trailing
    pytest arguments pass straight through, and pytest's exit code becomes
    ``donkey test``'s own, so it drops into CI unchanged. Needs the ``[test]``
    extra (a missing pytest is an install prompt, exit 1, not a ``blocked on
    verification`` message).
    """
    if importlib.util.find_spec("pytest") is None:
        typer.secho(
            'donkey test needs the [test] extra. Install it with:\n'
            '    pip install "donkey-kit[test]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1)

    argv = [sys.executable, "-m", "pytest", "--donkey-conformance"]
    if agent:
        argv += ["--agent", agent]
    argv += list(ctx.args)

    completed = subprocess.run(argv, check=False)
    raise typer.Exit(completed.returncode)


@app.command(hidden=True)
def publish(if_changed: bool = typer.Option(True, "--if-changed/--always")) -> None:
    """Publish code-first assets to Exchange (CI-only, BG §2.5)."""
    _blocked("Exchange publication mechanism + digest metadata (BG §2.5)")


@app.command(hidden=True)
def verify() -> None:
    """Check the live server against the Exchange descriptor (BG §2.5)."""
    _blocked("Exchange descriptor read + live introspection (BG §2.5)")


@app.command()
def mock(
    port: int = typer.Option(8080, "--port", help="TCP port to bind"),
    host: str = typer.Option("127.0.0.1", "--host", help="host/interface to bind"),
    scenario: list[str] = typer.Option(
        [],
        "--scenario",
        help=(
            "Fault-injection rule, repeatable. e.g. 'pii_block:every=5', "
            "'budget:limit=20000,window=60s', 'injection:on-pattern=ignore previous'."
        ),
    ),
) -> None:
    """Run the local gateway simulator (BG §1.4).

    Serves the same captured fixtures ``core.errors.classify()`` is tested
    against, so a stock client pointed at ``DONKEY_LLM_PROXY_URL=http://{host}:{port}``
    sees the real rejection shapes locally. Every response carries
    ``x-donkey-simulator: true`` — it is a fixture replay, never a real gateway.

    It REPLAYS captured shapes; it does NOT evaluate policy. It tests how your
    agent handles a refusal, never which prompts get refused — you choose the
    refusal (the ``donkey-sim/<shape>`` model sentinel or a ``--scenario`` rule),
    the simulator does not decide it. Testing against real policy configuration
    needs gateway-side dry-run mode (#250).

    ``--scenario`` scripts a specific failure on demand (#188): ``pii_block``
    fails every Nth call, ``injection`` matches request text, and ``budget``
    runs a real windowed token counter (429 on exhaustion). Repeatable.

    Needs the ``[local]`` extra (starlette + uvicorn); this is NOT one of the
    verification-gated platform commands, so a missing extra is an install
    prompt (exit 1), not a ``blocked on verification`` message (exit 3).
    """
    from ..simulator import server
    from ..simulator.app import SimulatorConfig
    from ..simulator.scenarios import ScenarioError, parse_scenarios

    try:
        scenarios = parse_scenarios(scenario)
    except ScenarioError as exc:
        typer.secho(f"Invalid --scenario: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    config = SimulatorConfig(scenarios=scenarios) if scenarios else None
    try:
        server.serve(host=host, port=port, config=config)
    except ImportError as exc:
        typer.secho(
            'The local gateway simulator needs the [local] extra. Install it with:\n'
            '    pip install "donkey-kit[local]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1) from exc


@app.command()
def doctor(
    ctx: typer.Context,
    model: str = typer.Option(
        "gpt-4o", "--model", help="model id to test against the proxy allow-list"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit machine-readable JSON"),
) -> None:
    """Diagnose governed access: config, credentials, gateway, model, budget (#202).

    Makes ONE real governed call and reads the result through the BG §1.2 error
    taxonomy to tell the three look-alike failures apart — wrong URL
    (``GatewayUnavailable``), wrong credentials (``AuthError``), and
    credentials-fine-but-model-rejected (the verified ``model_not_found``
    passthrough → ``UpstreamRequestError``). Each failure prints the remediation
    the exception itself carries (one source of wording), and the budget line
    always states how stale the reading is — the proxy has no budget endpoint, so
    doctor never implies live data.

    Exits non-zero if any check fails, so it works as a CI preflight. Needs the
    ``[llm]`` extra for the probe client (a missing extra is an install prompt,
    exit 1, not a ``blocked on verification`` message).
    """
    from ..core.errors import DonkeyError
    from . import doctor as _doctor

    # The global --json (before the subcommand) and the local --json (after it)
    # are equivalent — either turns on machine-readable output.
    as_json = as_json or bool((ctx.obj or {}).get("json"))

    try:
        checks = _doctor.run_diagnostics(model)
    except ImportError as exc:  # openai (the [llm] extra) not installed
        typer.secho(
            'donkey doctor needs the [llm] extra for the probe client. Install it with:\n'
            '    pip install "donkey-kit[llm]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1) from exc
    except DonkeyError as exc:
        # A config/probe failure that escaped the taxonomy mapping: surface it
        # rather than crash with a traceback.
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {"name": c.name, "level": c.level.value, "detail": c.detail,
                     "remediation": c.remediation}
                    for c in checks
                ],
                indent=2,
            )
        )
    else:
        typer.echo(_doctor.format_report(checks))

    if _doctor.has_failure(checks):
        raise typer.Exit(1)


def main() -> None:  # pragma: no cover
    try:
        app()
    except DonkeyError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
