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
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
def local_mcp_server(*, deny: str | None = None, ciba: str | None = None) -> Iterator[LocalServer]:
    """Start server.py on a free port, wait for READY, yield it, stop it on exit.
    ``deny`` names a tool whose ``tools/call`` gets the gateway's access denial;
    ``ciba`` (``TOOL=MODE``) gives it the OBO policy's CIBA step-up answer instead."""
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
                    *(["--ciba", ciba] if ciba else []),
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


@dataclass
class Redirect:
    """A server that answers every request with a redirect to another origin."""

    url: str
    #: The ``client_id`` / ``client_secret`` each request it answered carried.
    seen: list[dict[str, str | None]] = field(default_factory=list)


@contextmanager
def cross_origin_redirect(target: LocalServer) -> Iterator[Redirect]:
    """Yield a server on its own port, so another origin than ``target``'s, that
    answers every request with a ``307`` to the same path on ``target`` (#1415).
    Bind to its ``url``: ``target``'s record then shows what followed the redirect."""
    target_origin = target.url.split("/mcp/", 1)[0]
    redirect = Redirect(url="")

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            redirect.seen.append({k: self.headers.get(k) for k in ("client_id", "client_secret")})
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(307)
            self.send_header("location", f"{target_origin}{self.path}")
            self.send_header("content-length", "0")
            self.end_headers()

        do_GET = do_POST = do_DELETE = _answer

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    redirect.url = f"http://127.0.0.1:{server.server_address[1]}/mcp/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield redirect
    finally:
        server.shutdown()
        server.server_close()


def credentials_seen(record: Path) -> list[dict[str, Any]]:
    """The recorded requests that carried a ``client_id`` or ``client_secret``."""
    return [r for r in read_requests(record) if r.get("client_id") or r.get("client_secret")]


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
