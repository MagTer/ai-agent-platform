"""A minimal subset of Typer's public API used in tests."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Argument", "Option", "Typer", "testing"]


def __getattr__(name: str) -> Any:  # pragma: no cover - compatibility shim
    if name == "testing":
        from . import testing as _testing

        return _testing
    raise AttributeError(f"module 'typer' has no attribute {name!r}")


@dataclass
class _OptionMetadata:
    default: Any
    names: Sequence[str]
    help: str | None
    show_default: bool


@dataclass
class _ArgumentMetadata:
    default: Any
    help: str | None


class _OptionDefault:
    """Container returned by :func:`Option` used to defer metadata."""

    def __init__(self, meta: _OptionMetadata) -> None:
        self.meta = meta


class _ArgumentDefault:
    """Container returned by :func:`Argument` used to defer metadata."""

    def __init__(self, meta: _ArgumentMetadata) -> None:
        self.meta = meta


def Option(  # noqa: N802 - mirrors Typer's public API
    default: Any = ...,
    *names: str,
    help: str | None = None,
    show_default: bool = False,
) -> Any:
    """Declare an option for a command function."""

    meta = _OptionMetadata(
        default=default,
        names=names,
        help=help,
        show_default=show_default,
    )
    return _OptionDefault(meta)


def Argument(  # noqa: N802 - mirrors Typer's public API
    default: Any = ...,
    *names: str,
    help: str | None = None,
) -> Any:
    """Declare a positional argument for a command function."""

    meta = _ArgumentMetadata(default=default, help=help)
    return _ArgumentDefault(meta)


@dataclass
class _Parameter:
    name: str
    kind: str  # "option" or "argument"
    annotation: Any
    default: Any
    option_names: Sequence[str]
    help: str | None


@dataclass
class _Command:
    callback: Callable[..., Any]
    parameters: list[_Parameter]

    def invoke(self, args: list[str]) -> Any:
        values: dict[str, Any] = {}
        positionals: list[str] = []

        option_lookup: dict[str, _Parameter] = {}
        for param in self.parameters:
            if param.kind == "option":
                values[param.name] = param.default
                for opt_name in param.option_names:
                    option_lookup[opt_name] = param
            else:
                values[param.name] = param.default

        idx = 0
        while idx < len(args):
            current_arg = args[idx]
            if current_arg.startswith("--") and current_arg != "--":
                param = option_lookup.get(current_arg)
                if param is None and current_arg.startswith("--no-"):
                    positive = "--" + current_arg[5:]
                    param = option_lookup.get(positive)
                    if param and param.annotation is bool:
                        values[param.name] = False
                        idx += 1
                        continue
                if param is None:
                    raise ValueError(f"Unknown option '{current_arg}'")

                if param.annotation is bool:
                    values[param.name] = True
                    idx += 1
                    continue

                if "=" in current_arg:
                    _, _, attached = current_arg.partition("=")
                    values[param.name] = _convert_value(attached, param.annotation)
                    idx += 1
                    continue

                if idx + 1 >= len(args):
                    raise ValueError(f"Option '{current_arg}' requires a value")
                idx += 1
                values[param.name] = _convert_value(args[idx], param.annotation)
                idx += 1
                continue

            positionals.append(current_arg)
            idx += 1

        pos_index = 0
        for param in self.parameters:
            if param.kind != "argument":
                continue
            if pos_index >= len(positionals):
                break
            if param.annotation is list:
                values[param.name] = positionals[pos_index:]
                pos_index = len(positionals)
                break
            values[param.name] = _convert_value(positionals[pos_index], param.annotation)
            pos_index += 1

        return self.callback(**values)


def _convert_value(value: str, annotation: Any) -> Any:
    if annotation in (inspect._empty, str, list, None):
        return value
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        return value.lower() in {"1", "true", "t", "yes", "y"}
    return value


def _normalize_annotation(annotation: Any) -> Any:
    if isinstance(annotation, str):
        lowered = annotation.lower()
        if lowered == "bool":
            return bool
        if lowered == "int":
            return int
        if lowered == "float":
            return float
        if lowered in {"str", "string"}:
            return str
        if lowered.startswith("list"):
            return list
    return annotation


class Typer:
    """Simple command collection compatible with the tests."""

    def __init__(self, help: str | None = None) -> None:
        self.help = help or ""
        self._commands: dict[str, _Command] = {}

    def command(
        self, name: str | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            command_name = name or func.__name__.replace("_", "-")
            self._commands[command_name] = _Command(
                callback=func,
                parameters=self._extract_parameters(func),
            )
            return func

        return decorator

    def _extract_parameters(self, func: Callable[..., Any]) -> list[_Parameter]:
        params: list[_Parameter] = []
        signature = inspect.signature(func)
        for parameter in signature.parameters.values():
            annotation = _normalize_annotation(parameter.annotation)
            default = parameter.default

            if isinstance(default, _OptionDefault):
                option_names = list(default.meta.names)
                if not option_names:
                    option_names.append(f"--{parameter.name.replace('_', '-')}")
                if annotation is bool and len(option_names) == 1:
                    option_names.append(f"--no-{parameter.name.replace('_', '-')}")
                params.append(
                    _Parameter(
                        name=parameter.name,
                        kind="option",
                        annotation=annotation,
                        default=default.meta.default,
                        option_names=option_names,
                        help=default.meta.help,
                    )
                )
            elif isinstance(default, _ArgumentDefault):
                params.append(
                    _Parameter(
                        name=parameter.name,
                        kind="argument",
                        annotation=annotation,
                        default=default.meta.default,
                        option_names=(),
                        help=default.meta.help,
                    )
                )
            else:
                params.append(
                    _Parameter(
                        name=parameter.name,
                        kind="argument",
                        annotation=annotation,
                        default=None if default is inspect._empty else default,
                        option_names=(),
                        help=None,
                    )
                )
        return params

    def _dispatch(self, args: Iterable[str]) -> Any:
        iterator = list(args)
        if not iterator:
            raise SystemExit(0)
        command_name = iterator[0]
        command = self._commands.get(command_name)
        if command is None:
            raise SystemExit(1)
        return command.invoke(iterator[1:])

    def __call__(self, *args: str) -> Any:  # pragma: no cover - CLI entry point
        import sys

        return self._dispatch(args or sys.argv[1:])
