"""Argument parser behavior shared by installed x86QW entrypoints."""

from __future__ import annotations

import argparse
import sys


class FriendlyArgumentParser(argparse.ArgumentParser):
    """Render argparse failures with the stable Portuguese CLI contract."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


__all__ = ("FriendlyArgumentParser",)
