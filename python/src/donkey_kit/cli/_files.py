"""Safe writes of a generated config file, shared by the commands that write one.

A generated file is published only after it is complete: the content is staged
in a temporary file in the destination's own directory, then made visible in one
step. The destination policy (#1746):

- The final path component must not be a symlink, dangling or not, and must not
  be a directory. ``--force`` does not relax this. Ancestor directories may be
  symlinks; they are the user's chosen layout (``/tmp`` is one on macOS).
- A file named like a secret holder (``.donkey-kit.local.toml``, ``.env``,
  ``.env.*``, ``.envrc``, ``donkey.lock.local``) is never a destination: a
  generated, commit-ready file must not land where secrets are kept.
- Creating a file never replaces one that appeared meanwhile: it is published
  with ``os.link``, which fails when the name exists, so a competing creator
  wins and this write is refused (an exclusive create is the fallback where the
  filesystem has no hard links).
- Replacing an existing file is a same-directory ``os.replace`` that keeps the
  file's permission bits. That makes the switch atomic for readers, but it is
  not compare-and-swap. A caller that derived the content from the file's old
  bytes passes them as ``expected``, and the write is refused if the file no
  longer holds them just before the switch (``donkey scaffold``, #1748). That
  narrows the window without closing it: an edit another process saves between
  that last check and the replace is lost. Cooperative locking would not stop
  an editor either, so this limit is documented rather than claimed away.

The staged file is removed on every handled failure. Content is written as
UTF-8 with ``\\n`` line endings.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path

from ..core.config import LOCAL_TOML_NAME

__all__ = ["UnsafeDestination", "WriteConflict", "check_destination", "create_new", "replace"]

# Basenames that hold secrets in a DDK project, so never receive generated content.
_SECRET_NAMES = frozenset({LOCAL_TOML_NAME, ".env", ".envrc", "donkey.lock.local"})


class UnsafeDestination(Exception):
    """The destination may not be written: a symlink, a directory or a secret
    holder. The message names the path and why, never file content."""


class WriteConflict(Exception):
    """The destination changed while the write was staged; nothing was published."""


def check_destination(path: Path) -> None:
    """Raise :class:`UnsafeDestination` unless ``path`` may receive a generated file.

    A missing path is acceptable; its parent need not exist yet.
    """
    name = path.name
    if name in _SECRET_NAMES or name.startswith(".env."):
        raise UnsafeDestination(
            f"{path} is where secrets are kept; a generated file is never written there. "
            "Choose a different path."
        )
    if path.is_symlink():
        raise UnsafeDestination(
            f"{path} is a symlink; a generated file is never written through one. "
            "Remove the link or choose a different path."
        )
    if path.is_dir():
        raise UnsafeDestination(f"{path} is a directory; choose a file path instead.")


def create_new(path: Path, content: str) -> None:
    """Publish ``content`` at ``path``, which must not exist.

    Raises:
        UnsafeDestination: ``path`` is not an acceptable destination.
        WriteConflict: ``path`` appeared before the content could be published.
        OSError: The directory could not be created or written.
    """
    check_destination(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = _stage(path, content)
    try:
        try:
            os.link(staged, path)
        except FileExistsError as exc:
            raise WriteConflict(
                f"{path} was created by something else; left it untouched."
            ) from exc
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
                raise
            _create_exclusive(path, content)
    finally:
        _discard(staged)


def replace(path: Path, content: str, *, expected: bytes | None = None) -> None:
    """Replace the regular file at ``path`` with ``content``, keeping its mode.

    ``expected``, when given, is the bytes ``content`` was derived from: the
    replace is refused if the file holds anything else just before the switch.

    Raises:
        UnsafeDestination: ``path`` is not an acceptable destination.
        WriteConflict: ``path`` vanished, changed kind or no longer holds
            ``expected`` before the replace.
        OSError: The directory could not be written.
    """
    check_destination(path)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as exc:
        raise WriteConflict(f"{path} was removed by something else; nothing written.") from exc
    staged = _stage(path, content)
    try:
        os.chmod(staged, mode)
        # Re-check just before the switch: a symlink or directory swapped in
        # meanwhile must not be replaced (os.replace would follow neither, but a
        # directory makes it fail late, and a link would be silently overwritten).
        check_destination(path)
        try:
            current = path.read_bytes() if expected is not None else None
        except FileNotFoundError:
            current = None
        if not path.exists():
            raise WriteConflict(f"{path} was removed by something else; nothing written.")
        if expected is not None and current != expected:
            raise WriteConflict(f"{path} changed while the write was staged; nothing written.")
        os.replace(staged, path)
    finally:
        _discard(staged)


def _stage(path: Path, content: str) -> Path:
    """Write ``content`` to a fresh hidden file beside ``path`` and flush it.

    Created with mode ``0o666`` so the process umask applies, as it would to a
    plain ``open(path, "w")``.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(16):
        staged = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            fd = os.open(staged, flags, 0o666)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            _discard(staged)
            raise
        return staged
    raise FileExistsError(errno.EEXIST, "could not create a temporary file", str(path.parent))


def _create_exclusive(path: Path, content: str) -> None:
    """The no-hard-link fallback: an exclusive create, then the content."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o666)
    except FileExistsError as exc:
        raise WriteConflict(f"{path} was created by something else; left it untouched.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except BaseException:
        _discard(path)
        raise


def _discard(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
