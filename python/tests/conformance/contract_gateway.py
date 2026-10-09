"""A loopback LLM proxy for the adapter contract suite (#742). Not a test module.

It speaks every route an adapter sends to: ``/chat/completions`` and
``/responses`` (OpenAI), ``/v1/messages`` (Anthropic) and
``/models/<m>:generateContent`` / ``:streamGenerateContent`` (Gemini), buffered
or streamed. Refusals replay the captured ``pii-detected`` 403 and
``token-rate-limit`` 429 (the packaged ``anypoint/llm_proxy`` fixtures). It is a real
TCP server, so it also sees the requests of a framework that builds its own HTTP
client (CrewAI), not only those sent through the SDK's transport.

Success bodies replay live captures: the OpenAI ``/chat/completions`` and
``/responses`` routes, buffered and streamed, replay the 2026-10-08 OpenAI
upstream captures in ``tests/fixtures/anypoint/routes/`` (#894, #1044). Only the
Anthropic stream is still synthetic; no capture of it exists yet.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from donkey_kit.simulator.fixtures import fixture_bytes, parse_headers

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
_HOP_BY_HOP = {"content-length", "connection", "transfer-encoding"}
REQUEST_ID = "rid-contract"


def _strip(raw: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in raw.items() if k.lower() not in _HOP_BY_HOP}


def _fixture_headers(name: str) -> dict[str, str]:
    return _strip(parse_headers((_FIXTURES / name).read_text()))


def _proxy_headers(name: str) -> dict[str, str]:
    # The llm_proxy captures ship as simulator package data (#944).
    raw = fixture_bytes("anypoint/llm_proxy", name).decode()
    return _strip(parse_headers(raw))


def _sse(events: list[tuple[str | None, Any]]) -> bytes:
    out = []
    for name, data in events:
        line = f"event: {name}\n" if name else ""
        out.append(f"{line}data: {json.dumps(data)}\n\n")
    return "".join(out).encode()


def _route(name: str) -> tuple[dict[str, str], bytes]:
    """Headers and body of an OpenAI-upstream route capture (#894)."""
    body = next((_FIXTURES / "routes").glob(f"openai.{name}.body.*")).read_bytes()
    return _fixture_headers(f"routes/openai.{name}.headers.txt"), body


def _anthropic_stream(model: str) -> bytes:
    events: list[tuple[str | None, Any]] = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_contract", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "PONG"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return _sse(events)


@dataclass(frozen=True)
class Recorded:
    """One request the gateway received."""

    method: str
    path: str
    headers: dict[str, str]  # lower-cased names
    body: bytes


class Gateway:
    """A loopback proxy answering every request as ``mode`` says: ``"ok"`` (a
    route-shaped success), ``"pii"`` (the captured 403) or ``"budget"`` (the
    captured 429). ``requests`` records what arrived, in order."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.requests: list[Recorded] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.url = ""

    def start(self) -> None:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                record = Recorded(
                    self.command,
                    self.path,
                    {k.lower(): v for k, v in self.headers.items()},
                    body,
                )
                with gateway._lock:
                    gateway.requests.append(record)
                status, headers, payload = gateway._answer(record)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    def reset(self, mode: str = "ok") -> None:
        with self._lock:
            self.requests.clear()
            self.mode = mode

    def _answer(self, record: Recorded) -> tuple[int, dict[str, str], bytes]:
        if self.mode == "pii":
            headers = _proxy_headers("reject.pii-detected.headers.txt")
            body = fixture_bytes("anypoint/llm_proxy", "reject.pii-detected.body.json")
            return 403, headers, body
        if self.mode == "budget":
            return 429, _proxy_headers("reject.token-rate-limit.headers.txt"), b""
        return self._success(record)

    def _success(self, record: Recorded) -> tuple[int, dict[str, str], bytes]:
        try:
            payload = json.loads(record.body or b"{}")
        except ValueError:
            payload = {}
        model = str(payload.get("model") or "m")
        stream = payload.get("stream") is True
        path = record.path.split("?", 1)[0]
        ids = {"x-request-id": REQUEST_ID}
        sse = {"content-type": "text/event-stream", **ids}
        js = {"content-type": "application/json", **ids}
        if path.endswith(":streamGenerateContent"):
            body = (_FIXTURES / "gemini_inbound/responses.stream.body.txt").read_bytes()
            return 200, _fixture_headers("gemini_inbound/responses.stream.headers.txt") | ids, body
        if path.endswith(":generateContent"):
            body = (_FIXTURES / "gemini_inbound/responses.success.body.json").read_bytes()
            return 200, _fixture_headers("gemini_inbound/responses.success.headers.txt") | ids, body
        if path.endswith("/messages"):
            if stream:
                return 200, sse, _anthropic_stream(model)
            body = (_FIXTURES / "anthropic_inbound/responses.success.body.json").read_bytes()
            return 200, js, body
        route = "responses" if path.endswith("/responses") else "chat_completions"
        headers, body = _route(f"{route}.{'stream' if stream else 'success'}")
        return 200, headers | ids, body
