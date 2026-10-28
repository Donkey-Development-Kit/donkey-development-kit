"""Bind discovered MCP servers with every installed framework through a live gateway (#221).

``bind_toolset.py`` runs the bindings against local servers. This runs them
against MCP instances deployed to a gateway: ``donkey.tools.discover(governed=True)``
reads them from the org in ``ANYPOINT_CLIENT_ID`` / ``ANYPOINT_CLIENT_SECRET`` /
``ANYPOINT_ORG_ID`` (and ``DONKEY_ENVIRONMENT``), and each ``--bind FRAMEWORK=ASSET``
binds one instance with its own credential. Per pair, it checks that:

- ``filter(servers=[ASSET])`` lists and binds that instance only, every tool it
  lists is bound, and ``--call`` returns ``--want``;
- with ``--tokens``, another pair's token makes the bind raise ``AuthError``.

Credentials come from the environment or a file, never from the command line:

- ``--tokens PATH``: a JSON object of asset ID to bearer token, for instances
  behind JWT Validation (``BearerAuth``);
- without it: ``DONKEY_LLM_PROXY_CLIENT_ID`` / ``_CLIENT_SECRET``, the default
  ``gateway_auth`` for instances behind Client ID Enforcement.

From python/, in a venv with the SDK and the frameworks installed (README.md):

    /tmp/<venv>/bin/python scripts/mcp_binding/bind_live.py \\
        --bind langgraph=my-mcp --call calculate '{"expression": "6*7"}' --want 42 \\
        [--tokens /tmp/tokens.json]

Exit code 1 if a pair fails a check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bind_toolset import MODULES, Call, scenario

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import AuthError
from donkey_kit.tools.auth import BearerAuth, McpAuth


async def check(
    donkey: Donkey, fw: str, asset: str, auth: McpAuth | None, call: Call, want: str
) -> list[str]:
    """Discover, narrow to ``asset`` and bind it with ``fw``; the problems found."""
    ts = (await donkey.tools.discover(governed=True, auth=auth)).filter(servers=[asset])
    expect = sorted((await ts.describe()).name_map)
    if call[0] not in expect:
        return [f"{asset} lists {expect}, not {call[0]}"]
    return await scenario(fw, ts, expect, call=call, want=want)


async def refused(donkey: Donkey, asset: str, token: str) -> list[str]:
    """Binding ``asset`` with another instance's token must raise ``AuthError``."""
    auth = BearerAuth(StaticToken(token))
    ts = (await donkey.tools.discover(governed=True, auth=auth)).filter(servers=[asset])
    try:
        await ts.describe()
    except AuthError:
        return []
    return ["listed its tools with another instance's token"]


async def run(args: argparse.Namespace) -> bool:
    tokens: dict[str, str] = json.loads(Path(args.tokens).read_text()) if args.tokens else {}
    call: Call = (args.call[0], json.loads(args.call[1]))
    pairs = [tuple(pair.split("=", 1)) for pair in args.bind]
    failed = False
    async with Donkey(DonkeyConfig.resolve(telemetry=False)) as donkey:
        for fw, asset in pairs:
            auth = BearerAuth(StaticToken(tokens[asset])) if tokens else None
            problems = await check(donkey, fw, asset, auth, call, args.want)
            others = [t for a, t in tokens.items() if a != asset]
            if others:
                problems += [f"wrong token: {p}" for p in await refused(donkey, asset, others[0])]
            failed |= bool(problems)
            print(f"{fw:16} {asset:28} {'FAIL' if problems else 'OK'}")
            for problem in problems:
                print(f"    {problem}")
    return not failed


def main() -> int:
    """Bind every ``--bind`` pair through the gateway."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", nargs="+", required=True, metavar="FRAMEWORK=ASSET")
    parser.add_argument("--call", nargs=2, required=True, metavar=("TOOL", "JSON_ARGS"))
    parser.add_argument("--want", required=True, help="text the call's result must contain")
    parser.add_argument("--tokens", help="JSON file of asset ID to bearer token")
    args = parser.parse_args()
    for pair in args.bind:
        fw = pair.split("=", 1)[0]
        if "=" not in pair or fw not in MODULES:
            parser.error(
                f"--bind {pair!r}: expected FRAMEWORK=ASSET, FRAMEWORK in {sorted(MODULES)}"
            )
    return 0 if asyncio.run(run(args)) else 1


if __name__ == "__main__":
    sys.exit(main())
