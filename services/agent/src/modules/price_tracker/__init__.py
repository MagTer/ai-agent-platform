"""Price Tracker Module.

Tracks product prices across multiple Swedish retailers (grocery stores and pharmacies).
Provides price history, alerts, and comparison features.
"""

from modules.price_tracker.notifier import PriceNotifier
from modules.price_tracker.parser import PriceExtractionResult, PriceParser
from modules.price_tracker.scheduler import PriceCheckScheduler
from modules.price_tracker.service import PriceTrackerService

__all__ = [
    "PriceTrackerService",
    "PriceParser",
    "PriceExtractionResult",
    "PriceNotifier",
    "PriceCheckScheduler",
]


def get_price_tracker() -> PriceTrackerService:
    """Get the price tracker service instance.

    Note: This is a placeholder. The actual service should be instantiated
    with a session_factory via dependency injection.
    """
    raise NotImplementedError(
        "PriceTrackerService must be instantiated with a session_factory. "
        "Use dependency injection via core.providers."
    )
