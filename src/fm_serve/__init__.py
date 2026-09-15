"""fm-serve: always-on Apple Foundation Models (fm serve) with keepalive."""

__version__ = "0.1.0"


def main() -> None:
    import sys

    from fm_serve.cli import cli_main

    sys.exit(cli_main())
