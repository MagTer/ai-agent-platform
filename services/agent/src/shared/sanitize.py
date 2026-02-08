"""Sanitization helpers for user-controlled input in log messages.

Prevents log injection (CWE-117) by stripping control characters that could
forge log entries or break log parsers.
"""

from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_log(value: object) -> str:
    """Return a log-safe string representation of *value*.

    Strips newlines and ASCII control characters so that user-controlled
    input cannot inject additional log lines.
    """
    return _CONTROL_RE.sub("", str(value))
