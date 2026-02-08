"""Routing module for unified orchestration and request routing."""

from core.routing.guidance import DIRECT_ANSWER_PATTERNS, build_routing_guidance

__all__ = [
    "build_routing_guidance",
    "DIRECT_ANSWER_PATTERNS",
]
