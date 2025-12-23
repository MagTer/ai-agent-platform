from __future__ import annotations

import logging
from datetime import datetime

from .base import Tool

LOGGER = logging.getLogger(__name__)


class ClockTool(Tool):
    """Get the current date and time."""

    name = "clock"
    description = (
        "Get the current date and time. "
        "Returns the current local date and time in ISO 8601 format."
    )

    async def run(self) -> str:
        now = datetime.now()
        iso_format = now.isoformat()
        LOGGER.info(f"ClockTool called. Returning: {iso_format}")
        return iso_format
