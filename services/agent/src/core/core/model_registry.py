"""Model Capability Registry for handling different LLM reasoning formats."""

from __future__ import annotations

import logging
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)


class ReasoningMode(str, Enum):
    """How a model outputs reasoning content."""

    NONE = "none"  # No reasoning capability
    SEPARATE_FIELD = "separate_field"  # reasoning_content or thinking field
    INLINE_TAGS = "inline_tags"  # <think>...</think> in content


class ModelCapability(BaseModel):
    """Capability definition for a single model."""

    reasoning_mode: ReasoningMode = ReasoningMode.NONE
    reasoning_field: str | None = Field(
        default=None, description="Field name: reasoning_content, thinking, etc."
    )
    reasoning_tags: list[str] | None = Field(
        default=None, description="For inline mode: ['<think>', '</think>']"
    )
    fallback_to_reasoning: bool = Field(
        default=False, description="Use reasoning as content if content empty"
    )
    strip_inline_tags: bool = Field(default=True, description="Remove inline tags from content")


class ModelCapabilityRegistry:
    """Registry for model capabilities, loaded from config/models.yaml."""

    _instance: ModelCapabilityRegistry | None = None

    def __init__(self, config_path: Path | None = None) -> None:
        self._models: dict[str, ModelCapability] = {}
        self._aliases: dict[str, str] = {}
        self._defaults = ModelCapability()

        if config_path:
            self._load_config(config_path)

    @classmethod
    def get_instance(cls) -> ModelCapabilityRegistry:
        """Get singleton instance of the registry."""
        if cls._instance is None:
            config_path = Path(__file__).parent.parent.parent.parent / "config" / "models.yaml"
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        cls._instance = None

    def _load_config(self, path: Path) -> None:
        """Load configuration from YAML file."""
        if not path.exists():
            LOGGER.warning("Model config not found: %s", path)
            return

        with open(path) as f:
            config = yaml.safe_load(f)

        if not config:
            return

        if "defaults" in config:
            self._defaults = ModelCapability(**config["defaults"])

        for model_id, caps in config.get("models", {}).items():
            if caps:
                self._models[model_id] = ModelCapability(**caps)
            else:
                self._models[model_id] = ModelCapability()

        self._aliases = config.get("aliases", {})
        LOGGER.info(
            "Model registry loaded: %d models, %d aliases",
            len(self._models),
            len(self._aliases),
        )

    def get_capability(self, model: str) -> ModelCapability:
        """Get capability for a model, resolving aliases."""
        # Strip openrouter/ prefix if present
        model_id = model.removeprefix("openrouter/")

        # Check alias first
        if model_id in self._aliases:
            model_id = self._aliases[model_id]

        return self._models.get(model_id, self._defaults)

    def get_reasoning_field(self, model: str) -> str | None:
        """Get the reasoning field name for a model."""
        cap = self.get_capability(model)
        return cap.reasoning_field

    def should_fallback_to_reasoning(self, model: str) -> bool:
        """Check if model should use reasoning as content fallback."""
        cap = self.get_capability(model)
        return cap.fallback_to_reasoning

    def has_reasoning(self, model: str) -> bool:
        """Check if model has reasoning capability."""
        cap = self.get_capability(model)
        return cap.reasoning_mode != ReasoningMode.NONE

    def extract_inline_reasoning(self, content: str, model: str) -> tuple[str | None, str]:
        """Extract reasoning from inline tags like <think>...</think>.

        Returns:
            Tuple of (reasoning_content, clean_content)
        """
        cap = self.get_capability(model)

        if cap.reasoning_mode != ReasoningMode.INLINE_TAGS:
            return None, content

        tags = cap.reasoning_tags
        if not tags or len(tags) < 2:
            return None, content

        open_tag, close_tag = tags[0], tags[1]
        pattern = rf"{re.escape(open_tag)}(.*?){re.escape(close_tag)}"

        reasoning_parts: list[str] = []

        def extract(match: re.Match[str]) -> str:
            reasoning_parts.append(match.group(1))
            return ""

        clean_content = re.sub(pattern, extract, content, flags=re.DOTALL)
        reasoning = "".join(reasoning_parts) if reasoning_parts else None

        return reasoning, clean_content.strip()
