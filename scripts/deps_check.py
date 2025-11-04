#!/usr/bin/env python3
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
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass
class PackageUpdate:
    """Represents a single outdated dependency."""

    name: str
    current: str
    latest: str
    latest_status: str | None
    description: str | None

    @classmethod
    def from_mapping(cls, payload: dict) -> "PackageUpdate":
        return cls(
            name=payload.get("name", "<unknown>"),
            current=payload.get("version", "?"),
            latest=payload.get("latest", "?"),
            latest_status=payload.get("latest_status"),
            description=payload.get("description"),
        )


class PoetryError(RuntimeError):
    """Raised when the Poetry CLI cannot complete the request."""


def _run_poetry_show(extra_args: Sequence[str]) -> list[PackageUpdate]:
    if shutil.which("poetry") is None:
        raise PoetryError("Poetry executable not found in PATH.")

    command = [
        "poetry",
        "show",
        "--outdated",
        "--format",
        "json",
        *extra_args,
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        raise PoetryError(message)

    raw_output = result.stdout.strip()
    if not raw_output:
        return []

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise PoetryError(f"Failed to parse Poetry output: {exc}") from exc

    if not isinstance(payload, list):
        raise PoetryError("Unexpected Poetry output payload")

    return [PackageUpdate.from_mapping(item) for item in payload]


def _format_updates(updates: Iterable[PackageUpdate]) -> list[str]:
    rows: list[str] = []
    for update in updates:
        base = f"- {update.name} {update.current} → {update.latest}"
        if update.latest_status:
            base += f" ({update.latest_status})"
        if update.description:
            base += f" — {update.description}"
        rows.append(base)
    return rows


def check_dependencies(include_dev: bool = True) -> tuple[list[PackageUpdate], list[PackageUpdate], list[str]]:
    """Return runtime and development updates along with warning messages."""

    warnings: list[str] = []
    try:
        runtime_updates = _run_poetry_show(())
    except PoetryError as exc:
        raise PoetryError(f"Runtime dependency check failed: {exc}") from exc

    dev_updates: list[PackageUpdate] = []
    if include_dev:
        try:
            dev_updates = _run_poetry_show(("--only", "dev"))
        except PoetryError as exc:
            message = str(exc)
            # Poetry emits a specific message when the dependency group does not exist.
            if "No dependency group named" in message or "does not have dependency group" in message:
                warnings.append("Project does not define a 'dev' dependency group; skipping dev check.")
            else:
                raise PoetryError(f"Development dependency check failed: {exc}") from exc

    return runtime_updates, dev_updates, warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check for outdated Poetry dependencies")
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
            print(f"⚠️  {warning}")
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
            print("0 → All dependencies up to date.")
        else:
            messages = []
            if exit_code & 1:
                messages.append("runtime outdated")
            if exit_code & 2:
                messages.append("dev outdated")
            print(f"{exit_code} → {' and '.join(messages)}.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
