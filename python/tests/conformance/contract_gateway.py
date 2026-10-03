"""A loopback LLM proxy for the adapter contract suite (#742). Not a test module.

It speaks every route an adapter sends to: ``/chat/completions`` and
``/responses`` (OpenAI), ``/v1/messages`` (Anthropic) and
``/models/<m>:generateContent`` / ``:streamGenerateContent`` (Gemini), buffered
or streamed. Refusals replay the captured ``pii-detected`` 403 and
``token-rate-limit`` 429 (``tests/fixtures/anypoint/llm_proxy``). It is a real
TCP server, so it also sees the requests of a framework that builds its own HTTP
client (CrewAI), not only those sent through the SDK's transport.

Buffered success bodies replay the captured fixtures where one exists; the
OpenAI streams are synthetic but carry the event names and shapes the SDKs'
stream readers consume.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from donkey_kit.simulator.fixtures import parse_headers

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
_HOP_BY_HOP = {"content-length", "connection", "transfer-encoding"}
REQUEST_ID = "rid-contract"


def _fixture_headers(name: str) -> dict[str, str]:
    raw = parse_headers((_FIXTURES / name).read_text())
    return {k: v for k, v in raw.items() if k.lower() not in _HOP_BY_HOP}


def _sse(events: list[tuple[str | None, Any]], *, done: bool = False) -> bytes:
    out = []
    for name, data in events:
        line = f"event: {name}\n" if name else ""
        out.append(f"{line}data: {json.dumps(data)}\n\n")
    if done:
        out.append("data: [DONE]\n\n")
    return "".join(out).encode()


def _chat_completion(model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-contract",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "PONG"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _chat_stream(model: str) -> bytes:
    def chunk(delta: dict[str, Any], finish: str | None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-contract",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    usage = chunk({}, None) | {
        "choices": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    events: list[tuple[str | None, Any]] = [
        (None, chunk({"role": "assistant", "content": "PONG"}, None)),
        (None, chunk({}, "stop")),
        (None, usage),
    ]
    return _sse(events, done=True)


def _response(model: str) -> dict[str, Any]:
    return {
        "id": "resp_contract",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_contract",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "PONG", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


def _responses_stream(model: str) -> bytes:
    done = _response(model)
    pending = done | {"status": "in_progress", "output": [], "usage": None}
    item = done["output"][0]
    part = item["content"][0]
    events: list[tuple[str | None, Any]] = [
        ("response.created", {"type": "response.created", "sequence_number": 0,
                              "response": pending}),
        ("response.output_item.added", {
            "type": "response.output_item.added", "sequence_number": 1, "output_index": 0,
            "item": item | {"status": "in_progress", "content": []}}),
        ("response.content_part.added", {
            "type": "response.content_part.added", "sequence_number": 2,
            "item_id": item["id"], "output_index": 0, "content_index": 0,
            "part": part | {"text": ""}}),
        ("response.output_text.delta", {
            "type": "response.output_text.delta", "sequence_number": 3,
            "item_id": item["id"], "output_index": 0, "content_index": 0,
            "delta": "PONG", "logprobs": []}),
        ("response.output_text.done", {
            "type": "response.output_text.done", "sequence_number": 4,
            "item_id": item["id"], "output_index": 0, "content_index": 0,
            "text": "PONG", "logprobs": []}),
        ("response.content_part.done", {
            "type": "response.content_part.done", "sequence_number": 5,
            "item_id": item["id"], "output_index": 0, "content_index": 0, "part": part}),
        ("response.output_item.done", {
            "type": "response.output_item.done", "sequence_number": 6, "output_index": 0,
            "item": item}),
        ("response.completed", {"type": "response.completed", "sequence_number": 7,
                                "response": done}),
    ]
    return _sse(events)


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
            headers = _fixture_headers("llm_proxy/reject.pii-detected.headers.txt")
            body = (_FIXTURES / "llm_proxy/reject.pii-detected.body.json").read_bytes()
            return 403, headers, body
        if self.mode == "budget":
            return 429, _fixture_headers("llm_proxy/reject.token-rate-limit.headers.txt"), b""
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
        if path.endswith("/responses"):
            if stream:
                return 200, sse, _responses_stream(model)
            return 200, js, json.dumps(_response(model)).encode()
        if stream:
            return 200, sse, _chat_stream(model)
        return 200, js, json.dumps(_chat_completion(model)).encode()
