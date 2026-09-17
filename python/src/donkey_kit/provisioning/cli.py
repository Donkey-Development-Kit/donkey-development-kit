"""The ``donkey`` CLI (§5.2, §7).

Telemetry is on by default (§2.5, BG §1.6), but export stays inert unless an
OTLP endpoint is configured: set ``OTEL_EXPORTER_OTLP_ENDPOINT`` and spans flow
to your own sink with no SDK-specific env var; opt out entirely with
``DONKEY_TELEMETRY=false``. Commands that need a verified
platform API print an honest, actionable "blocked pending verification" message
and exit non-zero rather than fabricating calls (working instruction #2).
Commands that need no platform API (spec validation) do real work now.
"""

from __future__ import annotations

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

from ..core.errors import DonkeyError
from .spec import DonkeySpec

app = typer.Typer(
    add_completion=False,
    help="SDK for Agent Fabric — governed models, tools, provisioning-as-code.",
)


def _load_spec(file: Path) -> DonkeySpec:
    try:
        return DonkeySpec.from_yaml(file.read_text())
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        typer.secho(f"Invalid spec {file}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


def _blocked(what: str) -> None:
    typer.secho(f"blocked on verification: {what}", fg="yellow", err=True)
    typer.secho(
        "See docs/verified-apis.md — this command is scaffolded but not wired "
        "until the underlying platform API is confirmed against a sandbox (§0.3).",
        err=True,
    )
    raise typer.Exit(3)


@app.command()
def validate(file: Path = typer.Option(..., "-f", "--file", help="donkey.yaml")) -> None:
    """Validate a donkey.yaml against the schema (needs no platform API)."""
    spec = _load_spec(file)
    typer.secho(
        f"OK: {spec.metadata.name} — {len(spec.mcpBridges)} MCP bridge(s), "
        f"env {spec.metadata.environment}.",
        fg="green",
    )


@app.command()
def plan(file: Path = typer.Option(..., "-f", "--file"),
         dry_run: bool = typer.Option(False, "--dry-run"),
         out: Path | None = typer.Option(None, "--out", help="write plan.json for CI")) -> None:
    """Show the create/update/remove plan (read-before-write, §5.2)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning read API (§5.2, §5)")


@app.command()
def apply(file: Path = typer.Option(..., "-f", "--file"),
          auto_approve: bool = typer.Option(False, "--auto-approve")) -> None:
    """Apply the plan (CI-only, platform-controlled creds, §5.4)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning write API (§5.2, §5.4)")


@app.command()
def drift(file: Path = typer.Option(..., "-f", "--file")) -> None:
    """Compare live state against the spec; exit non-zero on drift (§5.2)."""
    _load_spec(file)
    _blocked("MCP Bridge provisioning read API (§5.2)")


@app.command()
def lint(file: Path = typer.Option(..., "-f", "--file")) -> None:
    """Governance lint (§5.3). Local spec-shape checks run now; ruleset
    resolution is gated (§0.3)."""
    _load_spec(file)
    _blocked("governance rulesets resolution API (§5.3, §0.3)")


@app.command()
def generate(file: Path = typer.Option(..., "-f", "--file"),
             target: str = typer.Option("terraform", "--target")) -> None:
    """Emit Terraform from the spec — the §5.5 pivot if provisioning is UI-only."""
    _load_spec(file)
    _blocked("Terraform provider coverage enumeration (§5.5, §0.3)")


@app.command()
def status() -> None:
    """Render published / reachable / governed per asset (§7.6)."""
    _blocked("Exchange + API Manager read APIs (§7.6)")


@app.command()
def init() -> None:
    """Scan the project, propose publishable assets, write .donkey-kit.toml (§7.8)."""
    _blocked("per-framework asset detection + descriptor derivation (§7.8, §7.3)")


@app.command()
def publish(if_changed: bool = typer.Option(True, "--if-changed/--always")) -> None:
    """Publish code-first assets to Exchange (CI-only, §7.5/§7.7)."""
    _blocked("Exchange publication mechanism + digest metadata (§7.5, §7.9)")


@app.command()
def verify() -> None:
    """Check the live server against the Exchange descriptor (§7.4)."""
    _blocked("Exchange descriptor read + live introspection (§7.4, §7.9)")


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


def main() -> None:  # pragma: no cover
    try:
        app()
    except DonkeyError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":  # pragma: no cover
    main()
