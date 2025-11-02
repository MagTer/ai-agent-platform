"""Minimal subset of :mod:`rich.table` for testing."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Table:
    title: str | None = None
    columns: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)

    def add_column(self, name: str) -> None:
        self.columns.append(name)

    def add_row(self, *values: str) -> None:
        self.rows.append([str(value) for value in values])

    def __str__(self) -> str:
        lines = []
        if self.title:
            lines.append(self.title)
        header = " | ".join(self.columns)
        lines.append(header)
        for row in self.rows:
            lines.append(" | ".join(row))
        return "\n".join(lines)


__all__ = ["Table"]
