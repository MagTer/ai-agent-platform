"""Compatibility wrapper that exposes the stack Typer CLI from ``src/stack``."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path


def main() -> None:  # pragma: no cover
    script_root = Path(__file__).resolve().parents[1]
    src_path = script_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    app = import_module("stack.cli").app
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
