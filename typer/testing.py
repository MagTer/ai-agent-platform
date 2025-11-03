"""Testing helpers that resemble :mod:`typer.testing`."""

from __future__ import annotations

import io
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass

from . import Typer


@dataclass(slots=True)
class Result:
    """Outcome of invoking a CLI command."""

    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None


class CliRunner:
    """Minimal CLI runner capturing stdout and stderr."""

    def invoke(self, app: Typer, args: Iterable[str] | None = None) -> Result:
        arguments = list(args or [])
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        exception: BaseException | None = None
        exit_code = 0

        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                app._dispatch(arguments)
        except SystemExit as exc:  # pragma: no cover - mirrors Typer behaviour
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except BaseException as exc:  # noqa: BLE001 - mirror Typer's capture
            exit_code = 1
            exception = exc
        finally:
            stdout = stdout_buffer.getvalue()
            stderr = stderr_buffer.getvalue()

        return Result(exit_code=exit_code, stdout=stdout, stderr=stderr, exception=exception)


__all__ = ["CliRunner", "Result"]
