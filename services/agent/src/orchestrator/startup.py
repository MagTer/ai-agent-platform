"""Application startup wiring -- composition root for module dependencies.

This is the ONLY place where concrete module implementations are imported
and registered with the core provider system. Moving these imports here
(from interfaces/http/app.py) keeps the interface layer clean of module
dependencies, satisfying the 4-layer architecture constraint:

    interfaces -> orchestrator -> modules -> core
"""

import logging
from typing import TYPE_CHECKING

from core.db.engine import AsyncSessionLocal
from core.providers import (
    get_fetcher,
    set_code_indexer_factory,
    set_email_service,
    set_embedder,
    set_fetcher,
    set_rag_manager,
    set_token_manager,
)
from modules.email.service import EmailConfig, ResendEmailService
from modules.embedder import LiteLLMEmbedder
from modules.fetcher import WebFetcher
from modules.homey.scheduler import HomeyDeviceSyncScheduler
from modules.indexer import CodeIndexer
from modules.price_tracker.scheduler import PriceCheckScheduler
from modules.rag import RAGManager

if TYPE_CHECKING:
    from shared.litellm_client import LiteLLMClient

    from core.auth.token_manager import TokenManager as TokenManagerType
    from core.runtime.config import Settings

LOGGER = logging.getLogger(__name__)


async def register_providers(
    settings: "Settings",
    litellm_client: "LiteLLMClient",
) -> "TokenManagerType":
    """Register all module implementations with the core provider system.

    Returns the TokenManager instance (needed by app.py for OAuth routes).
    """
    # 1. Embedder (LiteLLM proxy -> OpenRouter)
    embedder = LiteLLMEmbedder(litellm_client)
    set_embedder(embedder)

    # 2. RAG manager with embedder
    rag_manager = RAGManager(
        embedder=embedder,
        qdrant_url=str(settings.qdrant_url),
        collection_name=settings.qdrant_collection,
    )
    set_rag_manager(rag_manager)

    # 3. Fetcher with RAG manager
    fetcher = WebFetcher(rag_manager=rag_manager)
    set_fetcher(fetcher)

    # 4. Code indexer factory
    set_code_indexer_factory(CodeIndexer)

    # 5. OAuth TokenManager
    from core.auth.token_manager import TokenManager

    token_manager = TokenManager(AsyncSessionLocal, settings)
    set_token_manager(token_manager)

    LOGGER.info("Dependency providers registered")
    return token_manager


def create_email_service(settings: "Settings") -> ResendEmailService | None:
    """Create and register email service if configured."""
    if not settings.resend_api_key:
        return None

    email_config = EmailConfig(
        api_key=settings.resend_api_key,
        from_email=settings.email_from_address,
    )
    email_service = ResendEmailService(email_config)
    set_email_service(email_service)
    LOGGER.info("Email service initialized")
    return email_service


async def start_schedulers(
    email_service: ResendEmailService | None,
) -> tuple[PriceCheckScheduler, HomeyDeviceSyncScheduler]:
    """Create and start background schedulers.

    Returns scheduler instances so the caller can stop them on shutdown.
    """
    # Price Tracker Scheduler
    price_scheduler = PriceCheckScheduler(
        session_factory=AsyncSessionLocal,
        fetcher=get_fetcher(),
        email_service=email_service,
    )
    await price_scheduler.start()
    LOGGER.info("Price check scheduler started")

    # Homey Device Sync Scheduler
    homey_scheduler = HomeyDeviceSyncScheduler(
        session_factory=AsyncSessionLocal,
    )
    await homey_scheduler.start()
    LOGGER.info("Homey device sync scheduler started")

    return price_scheduler, homey_scheduler
