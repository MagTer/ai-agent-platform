"""Small subset of :mod:`rich.console` used in the CLI."""

from __future__ import annotations

from typing import Any

ANSI_MAP = {
    "reset": "\x1b[0m",
    "cyan": "\x1b[36m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
    "red": "\x1b[31m",
}


class Console:
    """Console that proxies ``print`` calls to the standard output."""

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        payload = sep.join(str(obj) for obj in objects)
        print(self._apply_colors(payload), end=end)

    def _apply_colors(self, payload: str) -> str:
        result: list[str] = []
        i = 0
        open_color = False
        while i < len(payload):
            if payload[i] == "[":
                closing = payload.find("]", i + 1)
                if closing != -1:
                    tag = payload[i + 1 : closing]
                    if tag.startswith("/"):
                        color_name = tag[1:]
                        if color_name in ANSI_MAP:
                            result.append(ANSI_MAP["reset"])
                            open_color = False
                            i = closing + 1
                            continue
                    elif tag in ANSI_MAP:
                        result.append(ANSI_MAP[tag])
                        open_color = True
                        i = closing + 1
                        continue
            result.append(payload[i])
            i += 1

        if open_color:
            result.append(ANSI_MAP["reset"])
        return "".join(result)


__all__ = ["Console"]
