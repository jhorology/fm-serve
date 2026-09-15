"""fm-serve: always-on Apple Foundation Models (fm serve) with keepalive."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fm-serve")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


def main() -> None:
    import sys

    from fm_serve.cli import cli_main

    sys.exit(cli_main())
