"""Command-line entry point; online commands arrive in a later milestone."""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mstodo-to-ics",
        description="Export Microsoft To Do lists to RFC 5545 VTODO calendars.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    """Run the currently scaffolded command-line interface."""
    parser = build_parser()
    parser.parse_args()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
