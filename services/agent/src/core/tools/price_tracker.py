"""Price tracker tool for OpenWebUI chat integration."""

from __future__ import annotations

import logging
from typing import Any

from core.tools.base import Tool

logger = logging.getLogger(__name__)


class PriceTrackerTool(Tool):
    """Tool for querying prices and deals via chat."""

    name = "price_tracker"
    description = """Kontrollera priser och erbjudanden pa matvaror och apoteksvaror.

Anvand for fragor som:
- "Vad kostar taco krydda just nu?"
- "Har ICA nagra bra erbjudanden?"
- "Jamfor priset pa Alvedon mellan Apotea och Med24"
- "Vilka produkter ar pa rea denna vecka?"
"""
    category = "domain"
    requires_confirmation = False
    activity_hint = {"product_query": "Kollar pris: {product_query}"}

    # Define tool schema for LLM
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check_price", "find_deals", "compare_stores", "list_products"],
                "description": "Typ av prisforfragan",
            },
            "product_query": {
                "type": "string",
                "description": "Produktnamn eller sokterm (t.ex. 'taco krydda', 'Alvedon')",
            },
            "store": {
                "type": "string",
                "enum": ["ica", "willys", "apotea", "med24", "all"],
                "description": "Butik att kontrollera (standard: all)",
            },
            "store_type": {
                "type": "string",
                "enum": ["grocery", "pharmacy", "all"],
                "description": "Typ av butik for erbjudanden (standard: all)",
            },
        },
        "required": ["action"],
    }

    async def run(
        self,
        action: str,
        product_query: str | None = None,
        store: str = "all",
        store_type: str = "all",
        **kwargs: Any,
    ) -> str:
        """Execute the price tracker action."""
        from core.providers import get_price_tracker

        try:
            service = get_price_tracker()
        except Exception as e:
            logger.warning(f"Price tracker not configured: {e}")
            return "Prisfunktionen är inte konfigurerad. Kontakta administratören."

        try:
            if action == "check_price":
                return await self._check_price(service, product_query, store)
            elif action == "find_deals":
                return await self._find_deals(service, store_type)
            elif action == "compare_stores":
                return await self._compare_stores(service, product_query)
            elif action == "list_products":
                return await self._list_products(service, product_query)
            else:
                return f"Okand aktion: {action}"
        except Exception as e:
            logger.error(f"Price tracker error: {e}", exc_info=True)
            return f"Ett fel uppstod vid priskontroll: {str(e)}"

    async def _check_price(
        self,
        service: Any,
        product_query: str | None,
        store: str,
    ) -> str:
        """Check current price for a product."""
        if not product_query:
            return "Ange en produkt att soka efter."

        # Search for products matching query
        store_id = None
        if store != "all":
            stores = await service.get_stores()
            for s in stores:
                if s["slug"] == store:
                    store_id = s["id"]
                    break

        products = await service.get_products(search=product_query, store_id=store_id)

        if not products:
            return f"Hittade inga produkter som matchar '{product_query}'."

        lines = [f"**Priser for '{product_query}':**\n"]

        for product in products[:5]:  # Limit to 5 products
            lines.append(f"**{product['name']}**")
            if product.get("brand"):
                lines.append(f"  Varumarke: {product['brand']}")

            # Get price history for this product
            history = await service.get_price_history(product["id"], days=1)

            if history:
                for price in history:
                    store_name = price.get("store_name", "")
                    current = price.get("offer_price_sek") or price.get("price_sek")

                    if current:
                        price_str = f"{current} kr"
                        if price.get("offer_type"):
                            price_str += f" ({price['offer_type']})"
                        lines.append(f"  - {store_name}: {price_str}")
            else:
                lines.append("  Ingen prisdata tillganglig")

            lines.append("")

        return "\n".join(lines)

    async def _find_deals(
        self,
        service: Any,
        store_type: str,
    ) -> str:
        """Find current deals/offers."""
        filter_type = None if store_type == "all" else store_type
        deals = await service.get_current_deals(store_type=filter_type)

        if not deals:
            filter_text = ""
            if store_type == "grocery":
                filter_text = " for matvaror"
            elif store_type == "pharmacy":
                filter_text = " for apoteksvaror"
            return f"Inga aktuella erbjudanden hittades{filter_text}."

        type_label = {
            "grocery": "matvaror",
            "pharmacy": "apoteksvaror",
            "all": "alla kategorier",
        }

        lines = [f"**Aktuella erbjudanden ({type_label.get(store_type, 'alla')}):**\n"]

        for deal in deals[:10]:  # Limit to 10 deals
            product_name = deal.get("product_name", "")
            store_name = deal.get("store_name", "")
            offer_price = deal.get("offer_price_sek", "")
            offer_type = deal.get("offer_type", "")
            offer_details = deal.get("offer_details", "")

            line = f"- **{product_name}** ({store_name}): {offer_price} kr"
            if offer_type:
                line += f" [{offer_type}]"
            if offer_details:
                line += f" - {offer_details}"

            lines.append(line)

        if len(deals) > 10:
            lines.append(f"\n*...och {len(deals) - 10} fler erbjudanden*")

        return "\n".join(lines)

    async def _compare_stores(
        self,
        service: Any,
        product_query: str | None,
    ) -> str:
        """Compare prices across stores for a product."""
        if not product_query:
            return "Ange en produkt att jamfora."

        products = await service.get_products(search=product_query)

        if not products:
            return f"Hittade inga produkter som matchar '{product_query}'."

        # Take first matching product
        product = products[0]
        history = await service.get_price_history(product["id"], days=1)

        if not history:
            return f"Ingen prisdata tillganglig for '{product['name']}'."

        lines = [f"**Prisjamforelse: {product['name']}**\n"]

        # Sort by price (lowest first)
        sorted_prices = sorted(
            history,
            key=lambda x: float(x.get("offer_price_sek") or x.get("price_sek") or 999999),
        )

        for i, price in enumerate(sorted_prices):
            store_name = price.get("store_name", "")
            current = price.get("offer_price_sek") or price.get("price_sek")

            if current:
                prefix = "**" if i == 0 else ""
                suffix = " (lagst!)**" if i == 0 else ""
                offer = f" [{price['offer_type']}]" if price.get("offer_type") else ""

                lines.append(f"- {prefix}{store_name}: {current} kr{offer}{suffix}")

        return "\n".join(lines)

    async def _list_products(
        self,
        service: Any,
        product_query: str | None,
    ) -> str:
        """List tracked products."""
        products = await service.get_products(search=product_query)

        if not products:
            if product_query:
                return f"Inga produkter matchar '{product_query}'."
            return "Inga produkter finns registrerade."

        lines = ["**Registrerade produkter:**\n"]

        for product in products[:15]:
            name = product["name"]
            brand = f" ({product['brand']})" if product.get("brand") else ""
            category = product.get("category", "")
            category_text = f" - {category}" if category else ""
            lines.append(f"- {name}{brand}{category_text}")

        if len(products) > 15:
            lines.append(f"\n*...och {len(products) - 15} fler produkter*")

        return "\n".join(lines)
