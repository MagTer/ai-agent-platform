"""LLM-based price extraction with cost optimization."""

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

# LiteLLM proxy base URL
LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "http://litellm:4000")


@dataclass
class PriceExtractionResult:
    """Result from price extraction."""

    price_sek: Decimal | None
    unit_price_sek: Decimal | None
    offer_price_sek: Decimal | None
    offer_type: str | None  # "stammispris", "extrapris", "kampanj", etc.
    offer_details: str | None  # "Kop 2 betala for 1"
    in_stock: bool
    confidence: float
    raw_response: dict[str, str | float | bool | None]


class PriceParser:
    """LLM-based price extractor with Haiku-first strategy."""

    HAIKU_MODEL = "haiku"  # Maps to claude-3-5-haiku via LiteLLM
    SONNET_MODEL = "sonnet"  # Maps to claude-4-5-sonnet via LiteLLM
    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self) -> None:
        self._store_hints: dict[str, str] = {}
        self._load_store_hints()

    def _load_store_hints(self) -> None:
        """Load store-specific parsing hints."""
        from modules.price_tracker.stores import get_store_hints

        self._store_hints = get_store_hints()

    async def extract_price(
        self,
        text_content: str,
        store_slug: str,
        product_name: str | None = None,
    ) -> PriceExtractionResult:
        """Extract price data using LLM with Haiku-first strategy."""

        store_hint = self._store_hints.get(store_slug, "")

        prompt = self._build_prompt(text_content, store_slug, store_hint, product_name)

        # Try Haiku first (cheapest)
        try:
            result = await self._extract_with_model(prompt, self.HAIKU_MODEL)

            if result.confidence < self.CONFIDENCE_THRESHOLD:
                logger.info(f"Low confidence ({result.confidence}), retrying with Sonnet")
                result = await self._extract_with_model(prompt, self.SONNET_MODEL)

            return result

        except Exception as e:
            logger.warning(f"Haiku extraction failed: {e}, trying Sonnet")
            return await self._extract_with_model(prompt, self.SONNET_MODEL)

    def _build_prompt(
        self,
        text_content: str,
        store_slug: str,
        store_hint: str,
        product_name: str | None,
    ) -> str:
        """Build extraction prompt."""
        product_context = f"Product being searched: {product_name}\n" if product_name else ""

        return f"""Extract product price information from this Swedish store page.

Store: {store_slug}
{product_context}
Store-specific parsing hints:
{store_hint}

Page content (truncated):
{text_content[:6000]}

Return a JSON object with exactly these fields:
- "price": Regular price in SEK as a number (e.g., 29.90), null if not found
- "unit_price": Price per kg/liter/piece if shown, null otherwise
- "offer_price": Discounted/campaign price if on sale, null if no discount
- "offer_type": Type of offer ("stammispris", "extrapris", "kampanj", "medlemspris"),
  null if no offer
- "offer_details": Offer description in Swedish (e.g., "Kop 3 betala for 2"), null if none
- "in_stock": boolean, true if available, false if out of stock
- "confidence": Your confidence in the extraction from 0.0 to 1.0

Only output the JSON object, no explanation or markdown."""

    async def _extract_with_model(self, prompt: str, model: str) -> PriceExtractionResult:
        """Extract using specified model via LiteLLM proxy."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{LITELLM_API_BASE}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        data = json.loads(content)

        return PriceExtractionResult(
            price_sek=Decimal(str(data["price"])) if data.get("price") else None,
            unit_price_sek=Decimal(str(data["unit_price"])) if data.get("unit_price") else None,
            offer_price_sek=(
                Decimal(str(data["offer_price"])) if data.get("offer_price") else None
            ),
            offer_type=data.get("offer_type"),
            offer_details=data.get("offer_details"),
            in_stock=data.get("in_stock", True),
            confidence=float(data.get("confidence", 0.5)),
            raw_response=data,
        )
