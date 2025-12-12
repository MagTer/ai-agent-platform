"""Declarative tool loading utilities."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from .base import Tool
from .registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolSpec:
    """Typed representation of a tool entry from ``tools.yaml``."""

    name: str
    dotted_path: str
    args: dict[str, Any]


def _resolve_callable(path: str) -> Callable[..., Tool]:
    """Resolve ``path`` to a class or factory callable."""

    module_name, _, attribute = path.rpartition(".")
    if not module_name:
        msg = f"Tool path '{path}' is invalid; expected 'module.Class'"
        raise ValueError(msg)
    module: ModuleType = importlib.import_module(module_name)
    factory: Any = getattr(module, attribute, None)
    if factory is None:
        msg = f"Tool factory '{path}' could not be resolved"
        raise ValueError(msg)
    return factory


def _coerce_specs(raw: Any) -> list[ToolSpec]:
    """Validate and normalise YAML data into :class:`ToolSpec` objects."""

    specs: list[ToolSpec] = []
    if not raw:
        return specs
    if not isinstance(raw, list):  # pragma: no cover - defensive parsing
        LOGGER.warning("tools.yaml should contain a list of tool specs; skipping invalid block")
        return specs
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            LOGGER.warning("Skipping tool entry %s because it is not a mapping", index)
            continue
        name = str(item.get("name", "")).strip()
        dotted_path = str(item.get("type", "")).strip()
        args = item.get("args", {})
        if not name or not dotted_path:
            LOGGER.warning("Tool entry %s missing name or type", index)
            continue
        if not isinstance(args, dict):
            LOGGER.warning("Tool entry %s has non-dict args; using empty defaults", index)
            args = {}
        specs.append(ToolSpec(name=name, dotted_path=dotted_path, args=args))
    return specs


def load_tool_registry(config_path: Path) -> ToolRegistry:
    """Load tools from ``config_path`` into a :class:`ToolRegistry`."""

    registry = ToolRegistry()
    if not config_path.exists():
        LOGGER.info("No tool configuration found at %s", config_path)
        return registry

    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - I/O errors depend on filesystem state
        LOGGER.error("Failed to read tool configuration: %s", exc)
        return registry

    for spec in _coerce_specs(raw_config):
        try:
            factory = _resolve_callable(spec.dotted_path)
            tool_candidate = factory(**spec.args)
        except Exception as exc:  # pragma: no cover - depends on user-provided config
            LOGGER.error("Unable to instantiate tool '%s': %s", spec.name, exc)
            continue
        if not isinstance(tool_candidate, Tool):
            LOGGER.error("Configured tool '%s' does not inherit from Tool", spec.name)
            continue
        # Allow configuration to alias the tool name without mutating class constants permanently.
        if getattr(tool_candidate, "name", None) != spec.name:
            try:
                tool_candidate.name = spec.name
            except AttributeError:  # pragma: no cover - rare case for slot-based implementations
                LOGGER.warning("Tool '%s' does not allow overriding the name attribute", spec.name)
        registry.register(tool_candidate)
    return registry


__all__ = ["load_tool_registry"]
