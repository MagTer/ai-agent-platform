"""Small subset of :mod:`rich.console` used in the CLI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Console:
    """Console that proxies ``print`` calls to the standard output."""

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        builtins_print = print
        builtins_print(*objects, sep=sep, end=end)


__all__ = ["Console"]
