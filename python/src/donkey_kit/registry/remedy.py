"""One wording for a missing declaration across ``scan``, ``publish``,
``verify``, ``preflight`` and ``expose`` (#1750): the table and key to set,
and, when ``donkey scaffold`` can add that table, the exact command.

The planners build their issue details from these helpers, so every command
names a table the same way (its header text, ``donkey.expose.services.x``)
and suggests a command that runs as printed: the ``--expose`` flag, each
``--set`` and the scanned root in that order, each argument shell-quoted. A
scaffold command is only suggested for a table the project file does not have
yet, since scaffold never edits an authored table (#1748). Failures that are
not a missing declaration (a wrong value, a bad source attribution, a live
refusal) keep their own fix and get no scaffold suggestion.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from ..core.toml_text import key

__all__ = ["scaffold_command", "set_argument", "table_name"]


def table_name(*parts: str) -> str:
    """A table's header text, ``donkey.publication`` or ``donkey.agents.x``."""
    return ".".join(key(part) for part in parts)


def set_argument(table: Sequence[str], name: str, value: str) -> str:
    """The ``--set`` argument for ``name`` in ``table``: the table's path under
    ``donkey``, the key and the value, ``publication.version=1.0.0``."""
    path = table[1:] if table and table[0] == "donkey" else table
    return f"{table_name(*path, name)}={value}"


def scaffold_command(
    root: str | Path | None = None, *, expose: bool = False, sets: Sequence[str] = ()
) -> str:
    """The ``donkey scaffold`` command line that adds the missing tables.

    ``root`` is the scanned path as given; the working directory (``.``) is
    left out. ``sets`` are :func:`set_argument` values. Every argument is
    shell-quoted, so the line runs as printed once its placeholder values
    (upper-case words such as ``GATEWAY``) are replaced.
    """
    words = ["donkey", "scaffold"]
    if expose:
        words.append("--expose")
    for argument in sets:
        words += ["--set", argument]
    if root is not None and str(root) not in ("", "."):
        words.append(str(root))
    return " ".join(shlex.quote(word) for word in words)
