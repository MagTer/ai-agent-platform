"""A very small subset of Typer's public API used in tests.

This module provides enough functionality for the stack CLI tests without
pulling in the full ``typer`` dependency.  It offers a basic command registry,
option/argument declarations, and a programmatic runner that mimics the parts
of Typer used by the project.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "Argument",
    "Option",
    "Typer",
]


@dataclass
class _OptionMetadata:
    default: Any
    names: Sequence[str]
    help: Optional[str]
    show_default: bool


@dataclass
class _ArgumentMetadata:
    default: Any
    help: Optional[str]


class _OptionDefault:
    """Container returned by :func:`Option` used to defer metadata."""

    def __init__(self, meta: _OptionMetadata):
        self.meta = meta


class _ArgumentDefault:
    """Container returned by :func:`Argument` used to defer metadata."""

    def __init__(self, meta: _ArgumentMetadata):
        self.meta = meta


def Option(
    default: Any = ...,
    *names: str,
    help: Optional[str] = None,
    show_default: bool = False,
) -> Any:
    """Declare an option for a command function.

    Only the metadata relevant to the project is captured.  The returned object
    is inspected when the command is registered and replaced with the real
    default value.
    """

    meta = _OptionMetadata(default=default, names=names, help=help, show_default=show_default)
    return _OptionDefault(meta)


def Argument(
    default: Any = ...,
    *names: str,
    help: Optional[str] = None,
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
    help: Optional[str]


@dataclass
class _Command:
    callback: Callable[..., Any]
    parameters: List[_Parameter]

    def invoke(self, args: List[str]) -> Any:
        values: Dict[str, Any] = {}
        positionals: List[str] = []

        # Prepare defaults and collect metadata for option parsing.
        option_lookup: Dict[str, _Parameter] = {}
        for param in self.parameters:
            if param.kind == "option":
                values[param.name] = param.default
                for opt_name in param.option_names:
                    option_lookup[opt_name] = param
            else:
                values[param.name] = param.default

        idx = 0
        while idx < len(args):
            token = args[idx]
            if token.startswith("--") and token != "--":
                param = option_lookup.get(token)
                if param is None and token.startswith("--no-"):
                    positive = "--" + token[5:]
                    param = option_lookup.get(positive)
                    if param and param.annotation is bool:
                        values[param.name] = False
                        idx += 1
                        continue
                if param is None:
                    raise ValueError(f"Unknown option '{token}'")

                if param.annotation is bool:
                    values[param.name] = True
                    idx += 1
                    continue

                if "=" in token:
                    _, _, attached = token.partition("=")
                    values[param.name] = _convert_value(attached, param.annotation)
                    idx += 1
                    continue

                if idx + 1 >= len(args):
                    raise ValueError(f"Option '{token}' requires a value")
                idx += 1
                values[param.name] = _convert_value(args[idx], param.annotation)
                idx += 1
                continue

            positionals.append(token)
            idx += 1

        # Assign positional arguments in order of appearance.
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

    def __init__(self, help: Optional[str] = None):
        self.help = help or ""
        self._commands: Dict[str, _Command] = {}

    def command(self, name: Optional[str] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            command_name = name or func.__name__.replace("_", "-")
            self._commands[command_name] = _Command(
                callback=func,
                parameters=self._extract_parameters(func),
            )
            return func

        return decorator

    def _extract_parameters(self, func: Callable[..., Any]) -> List[_Parameter]:
        params: List[_Parameter] = []
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
                # Required argument without Typer helpers
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

    def __call__(self, *args: str) -> Any:  # pragma: no cover - used for manual execution
        import sys

        return self._dispatch(args or sys.argv[1:])


# Testing helpers -------------------------------------------------------------------------
from . import testing  # noqa: E402  (import at end to avoid circular import)

__all__.append("testing")
