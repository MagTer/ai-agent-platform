"""Protocol definitions for dependency inversion.

This package contains abstract interfaces (Protocols) that allow `core/`
to depend on abstractions rather than concrete implementations from `modules/`.

The dependency flow is:
- `core/` imports only from `core/protocols/`
- `modules/` implements these protocols
- `interfaces/` wires them together at startup
"""

from .email import EmailMessage, EmailResult, IEmailService
from .embedder import IEmbedder
from .fetcher import IFetcher
from .indexer import ICodeIndexer
from .oauth import IOAuthClient
from .price_tracker import IPriceScheduler, IPriceTracker
from .rag import IRAGManager

__all__ = [
    "EmailMessage",
    "EmailResult",
    "IEmailService",
    "IEmbedder",
    "IFetcher",
    "ICodeIndexer",
    "IOAuthClient",
    "IPriceScheduler",
    "IPriceTracker",
    "IRAGManager",
]
