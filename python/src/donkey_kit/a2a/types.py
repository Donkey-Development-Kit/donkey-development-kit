"""The values a served agent receives and returns (`BG §2.9.1`, #224).

Frozen dataclasses (ADR 0001) with no ``a2a-sdk`` import, so naming them in a
type hint needs no ``[a2a]`` extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["A2AResult", "A2ATask"]


@dataclass(frozen=True)
class A2ATask:
    """One A2A task handed to a ``donkey.serve()`` handler.

    Attributes:
        input: The text parts of the caller's message, joined by newlines; empty
            when the message carries no text.
        data: The structured (data) parts of the message, in order.
        task_id: The A2A task ID the server assigned.
        context_id: The A2A context (conversation) ID the task belongs to.
        message: The native ``a2a.types.Message``, for any part this view
            leaves out (files, URLs, metadata).
    """

    input: str
    data: tuple[Any, ...]
    task_id: str
    context_id: str
    message: Any


@dataclass(frozen=True)
class A2AResult:
    """What a handler returns when a plain string is not enough.

    A handler may return a ``str`` instead, which is the same as
    ``A2AResult(text=...)``. Each field that is set becomes one artifact on the
    completed task.

    Attributes:
        text: A text artifact.
        data: A structured artifact: anything JSON-serialisable.
    """

    text: str | None = None
    data: Any = None
