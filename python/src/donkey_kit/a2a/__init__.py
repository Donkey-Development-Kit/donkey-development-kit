"""Serve a ``@donkey.agent`` over A2A, wrapping the official ``a2a-sdk``
(`BG §2.9.1`, #224).

The protocol is the ``a2a-sdk``'s, never reimplemented here: its request
handler, task store, task lifecycle and routes do the A2A work, and this package
adds what a bare server lacks. The agent card comes from the same
``@donkey.agent`` record ``donkey scan`` reads, so the two cannot drift, and the
listener binds to localhost unless told otherwise, so the gateway, not the
agent, is the public face.

``import donkey_kit.a2a`` needs no extra: the ``a2a-sdk`` loads only when
:func:`build_app` or :func:`serve` (``Donkey.serve()``) runs, and a missing ``[a2a]`` extra
raises an ``ImportError`` naming the ``pip install`` command.
"""

from __future__ import annotations

from ._server import build_app, serve
from .types import A2AResult, A2ATask

__all__ = ["A2AResult", "A2ATask", "build_app", "serve"]
