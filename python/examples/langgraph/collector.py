"""A tiny, dependency-free local OTLP/HTTP trace collector for the demo (#194/#199).

The zero-config export path (#194) sends every governed span over OTLP the
moment ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. To *show* that in a gateway-free
demo we need something on the other end of the wire — but standing up the real
``otel/opentelemetry-collector`` Docker image would break the "runs from
``pip install`` in under 15 minutes, no gateway" promise this demo exists to
prove. So this is a ~real collector in ~40 lines of stdlib: it terminates
OTLP/HTTP on a local port, decodes the protobuf export, counts the spans, and
returns the OTLP success envelope the exporter expects.

Decoding uses ``opentelemetry-proto`` — a transitive dependency of the
``[otel]`` extra's ``opentelemetry-exporter-otlp-proto-http``, so it is always
present wherever the exporter that feeds this collector is. If that proto
surface is ever unavailable we still terminate the connection and count the
export request (so the demo never crashes on a telemetry detail); we just can't
name the spans.

This is demo-only scaffolding: it is *not* part of the shipped SDK, makes no
durability/ordering guarantees, and speaks only the http/protobuf OTLP profile
the ``[otel]`` extra defaults to.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
        ExportTraceServiceResponse,
    )

    _PROTO_OK = True
except Exception:  # pragma: no cover - only if opentelemetry-proto is absent
    _PROTO_OK = False


class SpanSink:
    """Thread-safe tally of the spans this collector has received. The HTTP
    server runs request handlers on their own threads, so every mutation takes
    the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.span_count = 0
        self.export_requests = 0
        self.span_names: list[str] = []

    def record(self, body: bytes) -> None:
        with self._lock:
            self.export_requests += 1
            if not _PROTO_OK:
                return
            req = ExportTraceServiceRequest()
            req.ParseFromString(body)
            for resource_spans in req.resource_spans:
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        self.span_count += 1
                        self.span_names.append(span.name)


class LocalOTLPCollector:
    """A background OTLP/HTTP trace collector on an ephemeral localhost port.

    Point ``OTEL_EXPORTER_OTLP_ENDPOINT`` at :attr:`endpoint` and the http
    exporter will ``POST`` protobuf exports to ``<endpoint>/v1/traces``, which
    this server accepts, decodes, and acknowledges.
    """

    def __init__(self) -> None:
        self.sink = SpanSink()
        sink = self.sink

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                if self.path.rstrip("/").endswith("/v1/traces"):
                    sink.record(body)
                    payload = (
                        ExportTraceServiceResponse().SerializeToString()
                        if _PROTO_OK
                        else b""
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-protobuf")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_args: object) -> None:
                """Silence the default per-request stderr logging."""

        # Port 0 -> the OS assigns a free ephemeral port, so parallel demos and
        # CI runs never collide.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        """The base OTLP endpoint (no ``/v1/traces`` suffix) to hand to
        ``OTEL_EXPORTER_OTLP_ENDPOINT``; the exporter appends the signal path."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> LocalOTLPCollector:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)
