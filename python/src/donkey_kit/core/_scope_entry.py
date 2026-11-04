"""Per-entry reset tokens for the dual sync/async scopes (#1484).

``donkey.as_user()``, ``donkey.run()``, ``donkey.attribution()`` and
``donkey.cache()`` each bind contextvars on enter and reset them on exit. The
tokens those ``set()`` calls return belong to the *entry*, not to the scope
object: one object entered by two tasks or threads at once must not share them,
or the second entry overwrites the first's token, and the first exit then leaves
its value bound or resets a token from another context.

So the tokens live here, on a stack in a contextvar of their own. Each task and
thread has its own context, so concurrent entries of one object never see each
other's tokens, and nested re-entry on one task pops in LIFO order. Private:
the scopes are the public face.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

__all__ = ["pop_entry", "push_entry"]


class _Entry:
    """One entry of a scope: the tokens it set, and the token of its own push."""

    __slots__ = ("owner", "pushed", "tokens")

    def __init__(self, owner: object, tokens: list[Token[Any]]) -> None:
        self.owner = owner
        self.tokens = tokens
        self.pushed: Token[tuple[_Entry, ...]] | None = None


_entries: ContextVar[tuple[_Entry, ...]] = ContextVar("donkey_scope_entries", default=())


def push_entry(owner: object, tokens: list[Token[Any]]) -> None:
    """Record the tokens ``owner``'s entry just set, in the current context."""
    entry = _Entry(owner, tokens)
    entry.pushed = _entries.set((*_entries.get(), entry))


def pop_entry(owner: object) -> None:
    """Reset the tokens of ``owner``'s innermost entry in the current context,
    newest first, then drop the entry. A no-op when this context never entered
    ``owner``."""
    for entry in reversed(_entries.get()):
        if entry.owner is owner:
            break
    else:
        return
    for token in reversed(entry.tokens):
        token.var.reset(token)
    stack = _entries.get()
    if stack[-1] is entry and entry.pushed is not None:
        _entries.reset(entry.pushed)
    else:
        # Exited out of order (manual __exit__ calls): drop only this entry.
        _entries.set(tuple(e for e in stack if e is not entry))
