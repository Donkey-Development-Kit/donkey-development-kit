"""Install a built wheel into a clean virtualenv and import it (#767).

``twine check`` proves the long description renders; it does not prove the
package installs or imports. A PyPI upload cannot be undone, so both publish
workflows run this on the exact wheel they are about to upload. It:

* creates a fresh virtualenv (no site packages, nothing from this checkout);
* ``pip install``s the wheel into it, resolving the base dependencies the way
  a consumer's ``pip install donkey-kit`` would;
* from an empty working directory, in isolated mode (so the checkout can never
  shadow the installed package), imports ``donkey_kit`` and the ``Donkey``
  entry point, and checks the import came from the virtualenv's
  site-packages and that ``__version__`` equals the installed distribution's
  version and the expected one.

    python scripts/smoke_test_wheel.py dist/*.whl [--expect-version VERSION]

The workflows pass a glob, so it must expand to exactly one wheel: a second
one (a stale file, or a platform wheel nobody planned for) is an error rather
than something to install alongside and leave unchecked.
``--expect-version`` defaults to the version in the wheel's filename. Each
``--pip-arg=ARG`` is passed through to ``pip install`` (the unit test passes
``--pip-arg=--no-index`` to stay offline). Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import venv
from collections.abc import Sequence
from pathlib import Path

# Runs inside the clean virtualenv. argv[1] is the expected version.
_PROBE = """
import importlib.metadata
import sys
import sysconfig
from pathlib import Path

import donkey_kit
from donkey_kit import Donkey

expected = sys.argv[1]
installed = importlib.metadata.version("donkey-kit")
where = Path(donkey_kit.__file__).resolve()
site = Path(sysconfig.get_paths()["purelib"]).resolve()
if site not in where.parents:
    raise SystemExit(f"donkey_kit imported from {where}, not from {site}")
if not donkey_kit.__version__ == installed == expected:
    raise SystemExit(
        f"__version__ {donkey_kit.__version__!r}, installed {installed!r}, expected {expected!r}"
    )
if not callable(Donkey):
    raise SystemExit("donkey_kit.Donkey is not callable")
print(f"donkey-kit {installed} installs and imports from {where.parent}")
"""


def wheel_version(wheel: Path) -> str:
    """The version field of a wheel filename (PEP 427: name-version-...)."""
    parts = wheel.name.split("-")
    if wheel.suffix != ".whl" or len(parts) < 5:
        raise ValueError(f"{wheel.name} is not a wheel filename")
    return parts[1]


def _bin(env_dir: Path, name: str) -> Path:
    return env_dir / ("Scripts" if sys.platform == "win32" else "bin") / name


def smoke_test(wheel: Path, expected: str, pip_args: Sequence[str] = ()) -> None:
    """Install ``wheel`` into a fresh virtualenv and run the import probe there."""
    wheel = wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="ddk-wheel-smoke-") as scratch:
        env_dir = Path(scratch) / "venv"
        empty_cwd = Path(scratch) / "cwd"
        empty_cwd.mkdir()
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        python = str(_bin(env_dir, "python"))
        subprocess.run(
            [python, "-m", "pip", "install", "--disable-pip-version-check", *pip_args, str(wheel)],
            check=True,
            cwd=empty_cwd,
        )
        subprocess.run([python, "-I", "-c", _PROBE, expected], check=True, cwd=empty_cwd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheels", type=Path, nargs="+", help="the built .whl (exactly one)")
    parser.add_argument("--expect-version", help="defaults to the wheel filename's version")
    parser.add_argument(
        "--pip-arg",
        action="append",
        default=[],
        dest="pip_args",
        metavar="ARG",
        help="extra `pip install` argument; repeatable (write --pip-arg=--flag)",
    )
    args = parser.parse_args(argv)

    if len(args.wheels) != 1:
        names = ", ".join(w.name for w in args.wheels)
        count = len(args.wheels)
        print(f"error: expected exactly one wheel, got {count}: {names}", file=sys.stderr)
        return 1
    (wheel,) = args.wheels
    expected = args.expect_version or wheel_version(wheel)
    try:
        smoke_test(wheel, expected, args.pip_args)
    except subprocess.CalledProcessError as exc:
        print(f"error: wheel smoke test failed ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
