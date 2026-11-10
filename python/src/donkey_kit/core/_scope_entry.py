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

The stack itself is never restored with ``_entries.reset``: a pop always sets
the stack minus the popped entry (#1674). A saved reset token would capture
the whole stack at push time, so after an out-of-order exit (manual
``__exit__`` calls: push A, push B, pop A) resetting B's token would bring A
back, and a second pop of A would reuse A's spent value tokens.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

__all__ = ["pop_entry", "push_entry"]


class _Entry:
    """One entry of a scope: its owner and the tokens it set. Compared by
    identity, so two entries of one owner stay distinct."""

    __slots__ = ("owner", "tokens")

    def __init__(self, owner: object, tokens: list[Token[Any]]) -> None:
        self.owner = owner
        self.tokens = tokens


_entries: ContextVar[tuple[_Entry, ...]] = ContextVar("donkey_scope_entries", default=())


def push_entry(owner: object, tokens: list[Token[Any]]) -> None:
    """Record the tokens ``owner``'s entry just set, in the current context."""
    _entries.set((*_entries.get(), _Entry(owner, tokens)))


def pop_entry(owner: object) -> None:
    """Reset the tokens of ``owner``'s innermost entry in the current context,
    newest first, then drop the entry. A no-op when this context never entered
    ``owner``."""
    stack = _entries.get()
    for entry in reversed(stack):
        if entry.owner is owner:
            break
    else:
        return
    # Drop the entry before resetting its tokens, so a reset that raises can
    # never leave a spent entry behind for a later pop to reuse.
    _entries.set(tuple(e for e in stack if e is not entry))
    for token in reversed(entry.tokens):
        token.var.reset(token)
