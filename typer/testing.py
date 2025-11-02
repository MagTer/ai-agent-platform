"""Testing helpers that resemble :mod:`typer.testing`."""
from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Iterable, Optional

from . import Typer


@dataclass
class Result:
    exit_code: int
    stdout: str
    stderr: str
    exception: Optional[BaseException]


class CliRunner:
    """Minimal CLI runner capturing stdout/stderr."""

    def invoke(self, app: Typer, args: Optional[Iterable[str]] = None) -> Result:
        arguments = list(args or [])
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        exception: Optional[BaseException] = None
        exit_code = 0

        try:
            from contextlib import redirect_stdout, redirect_stderr

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                app._dispatch(arguments)
        except SystemExit as exc:  # pragma: no cover - mirrors Typer behaviour
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except BaseException as exc:  # noqa: BLE001 - we need to mirror Typer's capture
            exit_code = 1
            exception = exc
        finally:
            stdout = stdout_buffer.getvalue()
            stderr = stderr_buffer.getvalue()

        return Result(exit_code=exit_code, stdout=stdout, stderr=stderr, exception=exception)


__all__ = ["CliRunner", "Result"]
