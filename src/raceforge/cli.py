"""Command-line entry point (`raceforge`)."""

import argparse

from raceforge import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="raceforge", description="RaceForge command-line interface"
    )
    parser.add_argument("--version", action="version", version=f"raceforge {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
