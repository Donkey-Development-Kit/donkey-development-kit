"""Helpers the §9 framework probes import (stdlib only; the server needs ``mcp``).

from _harness import local_mcp_server, headers_seen, distributions, write_fixture

with local_mcp_server() as srv:
    url, record = srv.url, srv.record     # http://127.0.0.1:<port>/mcp/
    ... bind the framework to url, send fake client_id/client_secret headers ...
    seen = headers_seen(record)
write_fixture("<framework>", {...})
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

SERVER = Path(__file__).resolve().parent / "server.py"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mcp_binding"


@dataclass(frozen=True)
class LocalServer:
    """A running local MCP server: its URL, record file and process."""

    url: str
    record: Path
    process: subprocess.Popen[str]


def free_port() -> int:
    """Ask the OS for a free TCP port on 127.0.0.1."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop_server(process: subprocess.Popen[str]) -> None:
    """SIGTERM the server and wait; kill it if it will not exit."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if process.stdout is not None:
        process.stdout.close()


@contextmanager
def local_mcp_server(*, deny: str | None = None) -> Iterator[LocalServer]:
    """Start server.py on a free port, wait for READY, yield it, stop it on exit.
    ``deny`` names a tool whose ``tools/call`` gets the gateway's access denial."""
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="ddk-mcpbind-") as tmp:
        record = Path(tmp) / "requests.jsonl"
        log = Path(tmp) / "server.log"
        with log.open("w", encoding="utf-8") as log_fh:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--port",
                    str(port),
                    "--record",
                    str(record),
                    *(["--deny", deny] if deny else []),
                ],
                stdout=subprocess.PIPE,
                stderr=log_fh,
                text=True,
            )
        try:
            if process.stdout is None:
                raise RuntimeError("server stdout was not captured")
            line = process.stdout.readline()
            if not line.startswith("READY"):
                raise RuntimeError(
                    f"server did not become ready: {line!r}\n{log.read_text(encoding='utf-8')}"
                )
            yield LocalServer(f"http://127.0.0.1:{port}/mcp/", record, process)
        finally:
            stop_server(process)


def read_requests(record: Path) -> list[dict[str, Any]]:
    """The requests the server recorded so far, oldest first."""
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]


def headers_seen(record: Path) -> dict[str, str | None]:
    """The ``client_id`` / ``client_secret`` value every recorded request carried.

    A key is ``None`` unless every request, the tool call's included, carried the
    same value for it, so one request that dropped a header fails the probe.
    """
    requests = read_requests(record)
    seen: dict[str, str | None] = {}
    for key in ("client_id", "client_secret"):
        values = {entry.get(key) for entry in requests}
        seen[key] = values.pop() if len(values) == 1 else None
    return seen


def distributions(*names: str) -> dict[str, str]:
    """Installed version of each named distribution (``mcp`` is always included)."""
    return {name: metadata.version(name) for name in sorted({"mcp", *names})}


def write_fixture(name: str, data: dict[str, Any]) -> Path:
    """Write ``tests/fixtures/mcp_binding/<name>.json`` (sorted keys, indent 2)."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
