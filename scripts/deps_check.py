#!/usr/bin/env python3
# ruff: noqa: S603
"""Dependency freshness checker.

This script shells out to ``poetry show --outdated`` to report outdated
runtime and development dependencies. It exits with codes that make it
useful in CI pipelines as well as local workflows.

Exit codes
---------
0
    No outdated dependencies detected.
1
    One or more runtime dependencies are outdated.
2
    One or more development dependencies are outdated.
3
    Both runtime and development dependencies are outdated.
4
    Poetry could not complete the check (for example because the
    ``poetry`` executable is missing or the command failed).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass
class PackageUpdate:
    """Represents a single outdated dependency."""

    name: str
    current: str
    latest: str
    latest_status: str | None
    description: str | None

    @classmethod
    def from_mapping(cls, payload: dict) -> PackageUpdate:
        return cls(
            name=payload.get("name", "<unknown>"),
            current=payload.get("version", "?"),
            latest=payload.get("latest", "?"),
            latest_status=payload.get("latest_status"),
            description=payload.get("description"),
        )


class PoetryError(RuntimeError):
    """Raised when the Poetry CLI cannot complete the request."""


_POETRY_COMMAND: list[str] | None = None
_POETRY_SUPPORTS_FORMAT: bool | None = None


def _get_poetry_command() -> list[str]:
    """Return the command (with args) used to invoke Poetry."""

    global _POETRY_COMMAND

    if _POETRY_COMMAND is not None:
        return _POETRY_COMMAND

    env_poetry = os.environ.get("POETRY_EXECUTABLE")
    if env_poetry:
        _POETRY_COMMAND = [env_poetry]
        return _POETRY_COMMAND

    poetry_path = shutil.which("poetry")
    if poetry_path is None:
        poetry_path = shutil.which("poetry.exe")

    if poetry_path is not None:
        _POETRY_COMMAND = [poetry_path]
    else:
        _POETRY_COMMAND = [sys.executable, "-m", "poetry"]

    return _POETRY_COMMAND


def _poetry_supports_format_option() -> bool:
    """Return whether the installed Poetry exposes the ``--format`` flag."""

    global _POETRY_SUPPORTS_FORMAT

    if _POETRY_SUPPORTS_FORMAT is not None:
        return _POETRY_SUPPORTS_FORMAT

    try:
        command = _get_poetry_command().copy()
        command.extend(["show", "--help"])
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Unknown error"
        raise PoetryError(message) from exc

    help_text = result.stdout
    _POETRY_SUPPORTS_FORMAT = "--format" in help_text
    return _POETRY_SUPPORTS_FORMAT


def _parse_plain_show_output(raw_output: str) -> list[PackageUpdate]:
    """Parse the plain-text output produced by ``poetry show --outdated``."""

    updates: list[PackageUpdate] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip table headers or separators produced by older Poetry versions.
        lowered = stripped.lower()
        if lowered.startswith("package") and "current" in lowered:
            continue
        if set(stripped) == {"-"}:
            continue

        parts = stripped.split(None, 3)
        if len(parts) < 3:
            # If we cannot confidently parse the row, skip it instead of failing
            # the entire check. The fallback mode is best-effort.
            continue

        name, current, latest = parts[:3]
        remainder = parts[3] if len(parts) == 4 else None

        latest_status: str | None = None
        description: str | None = None

        if remainder:
            # Poetry sometimes renders status information such as "latest" or
            # "up to date" within parentheses before the description. Extract
            # that marker if present while keeping the remaining text as the
            # package description.
            if remainder.startswith("(") and ")" in remainder:
                status, _, rest = remainder.partition(")")
                latest_status = status.strip("() ") or None
                description = rest.strip() or None
            else:
                description = remainder.strip() or None

        updates.append(
            PackageUpdate(
                name=name,
                current=current,
                latest=latest,
                latest_status=latest_status,
                description=description,
            ),
        )

    return updates


def _run_poetry_show(*, dev: bool = False) -> list[PackageUpdate]:
    base_command = _get_poetry_command().copy()
    base_command.extend(
        [
            "show",
            "--outdated",
        ],
    )
    if dev:
        base_command.extend(["--only", "dev"])

    use_json = _poetry_supports_format_option()
    command = base_command.copy()
    if use_json:
        command.extend(["--format", "json"])

    try:
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Unknown error"
        if use_json and "--format" in message.lower():
            # Older Poetry versions advertise --format inconsistently. Retry
            # without the flag so we can fall back to parsing plain output.
            use_json = False
        else:
            raise PoetryError(message) from exc
    else:
        raw_output = result.stdout.strip()
        if not raw_output:
            return []

        if use_json:
            try:
                payload = json.loads(raw_output)
            except json.JSONDecodeError:
                return _parse_plain_show_output(raw_output)

            if not isinstance(payload, list):
                raise PoetryError("Unexpected Poetry output payload")

            return [PackageUpdate.from_mapping(item) for item in payload]

        return _parse_plain_show_output(raw_output)

    # Fallback to plain-text parsing when the format option is unsupported.
    try:
        result = subprocess.run(  # noqa: S603
            base_command,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Unknown error"
        raise PoetryError(message) from exc

    raw_output = result.stdout.strip()
    if not raw_output:
        return []

    return _parse_plain_show_output(raw_output)


def _format_updates(updates: Iterable[PackageUpdate]) -> list[str]:
    rows: list[str] = []
    for update in updates:
        base = f"- {update.name} {update.current} -> {update.latest}"
        if update.latest_status:
            base += f" ({update.latest_status})"
        if update.description:
            base += f" - {update.description}"
        rows.append(base)
    return rows


def check_dependencies(
    include_dev: bool = True,
) -> tuple[list[PackageUpdate], list[PackageUpdate], list[str]]:
    """Return runtime and development updates along with warning messages."""

    warnings: list[str] = []
    try:
        runtime_updates = _run_poetry_show()
    except PoetryError as exc:
        raise PoetryError(f"Runtime dependency check failed: {exc}") from exc

    dev_updates: list[PackageUpdate] = []
    if include_dev:
        try:
            dev_updates = _run_poetry_show(dev=True)
        except PoetryError as exc:
            message = str(exc)
            # Poetry emits a specific message when the dependency group does not exist.
            if (
                "No dependency group named" in message
                or "does not have dependency group" in message
            ):
                warnings.append(
                    "Project does not define a 'dev' dependency group; skipping dev check.",
                )
            else:
                raise PoetryError(f"Development dependency check failed: {exc}") from exc

    return runtime_updates, dev_updates, warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check for outdated Poetry dependencies",
    )
    parser.add_argument(
        "--skip-dev",
        action="store_true",
        help="Skip checking the 'dev' dependency group.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only emit warnings and the exit code (useful for machine parsing).",
    )
    args = parser.parse_args(argv)

    try:
        runtime_updates, dev_updates, warnings = check_dependencies(include_dev=not args.skip_dev)
    except PoetryError as exc:
        print(exc, file=sys.stderr)
        return 4

    exit_code = 0
    if runtime_updates:
        exit_code |= 1
    if dev_updates:
        exit_code |= 2

    if not args.quiet:
        print("Dependency freshness report\n===========================")

        for warning in warnings:
            print(f"[warning] {warning}")
            print()

        print("Runtime dependencies:")
        if runtime_updates:
            print("\n".join(_format_updates(runtime_updates)))
        else:
            print("- All runtime dependencies are up to date.")
        print()

        if not args.skip_dev:
            print("Development dependencies:")
            if dev_updates:
                print("\n".join(_format_updates(dev_updates)))
            else:
                print("- All development dependencies are up to date.")
            print()

        print("Exit code summary:")
        if exit_code == 0:
            print("0 -> All dependencies up to date.")
        else:
            messages = []
            if exit_code & 1:
                messages.append("runtime outdated")
            if exit_code & 2:
                messages.append("dev outdated")
            print(f"{exit_code} -> {' and '.join(messages)}.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
