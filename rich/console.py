"""Small subset of :mod:`rich.console` used in the CLI."""
from __future__ import annotations

from typing import Any


class Console:
    """Console that proxies ``print`` calls to the standard output."""

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        print(*objects, sep=sep, end=end)


__all__ = ["Console"]
