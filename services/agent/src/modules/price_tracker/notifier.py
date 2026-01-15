"""Email notifications via Resend API."""

import logging
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)


class PriceNotifier:
    """Send price alerts via Resend email API."""

    RESEND_API_URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, from_email: str) -> None:
        self.api_key = api_key
        self.from_email = from_email

    async def send_price_alert(
        self,
        to_email: str,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None = None,
        price_drop_percent: float | None = None,
    ) -> bool:
        """Send price drop alert email."""
        subject = f"Prisvarning: {product_name} hos {store_name}"
        html_body = self._build_alert_html(
            product_name=product_name,
            store_name=store_name,
            current_price=current_price,
            target_price=target_price,
            offer_type=offer_type,
            offer_details=offer_details,
            product_url=product_url,
            price_drop_percent=price_drop_percent,
        )
        return await self._send_email(to_email, subject, html_body)

    async def send_weekly_summary(
        self,
        to_email: str,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> bool:
        """Send weekly price summary email."""
        subject = "Veckans prisoversikt - Prisspaning"
        html_body = self._build_summary_html(deals, watched_products)
        return await self._send_email(to_email, subject, html_body)

    def _build_alert_html(
        self,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None,
        price_drop_percent: float | None = None,
    ) -> str:
        """Build HTML for price alert email."""
        target_row = ""
        if target_price:
            target_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Ditt malpris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">{target_price} kr</td>
            </tr>"""

        price_drop_row = ""
        if price_drop_percent is not None:
            price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Prisfall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {price_drop_percent:.1f}% under ordinarie pris
                    </strong>
                </td>
            </tr>"""

        offer_row = ""
        if offer_type:
            details = f" - {offer_details}" if offer_details else ""
            offer_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Erbjudande:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <span style="background: #22c55e; color: white; padding: 2px 8px;
                                border-radius: 4px;">
                        {offer_type}
                    </span>{details}
                </td>
            </tr>"""

        link_section = ""
        if product_url:
            link_section = f"""
            <p style="margin-top: 20px;">
                <a href="{product_url}"
                   style="background: #2563eb; color: white; padding: 10px 20px;
                          text-decoration: none; border-radius: 4px;">Se produkten</a>
            </p>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Prisvarning!</h2>
            <p><strong>{product_name}</strong> hos <strong>{store_name}</strong>
               har ett bra pris.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">Aktuellt pris:</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 1.2em;">
                        <strong style="color: #22c55e;">{current_price} kr</strong>
                    </td>
                </tr>
                {target_row}
                {price_drop_row}
                {offer_row}
            </table>
            {link_section}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning. Du far detta for att du bevakar produkten.
            </p>
        </body>
        </html>
        """

    def _build_summary_html(
        self,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> str:
        """Build HTML for weekly summary email."""
        # Build deals section
        deals_html = ""
        if deals:
            deals_rows = ""
            for deal in deals[:10]:  # Limit to top 10
                product_name = deal.get("product_name", "")
                store_name = deal.get("store_name", "")
                offer_price = deal.get("offer_price_sek", "")
                offer_type = deal.get("offer_type", "")
                deals_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {product_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {store_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;
                               color: #22c55e; font-weight: bold;">
                        {offer_price} kr
                    </td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        <span style="background: #f59e0b; color: white;
                                     padding: 2px 6px; border-radius: 3px;
                                     font-size: 0.8em;">
                            {offer_type}
                        </span>
                    </td>
                </tr>"""

            deals_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Aktuella erbjudanden</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                        <th style="padding: 8px; text-align: left;">Pris</th>
                        <th style="padding: 8px; text-align: left;">Typ</th>
                    </tr>
                </thead>
                <tbody>{deals_rows}</tbody>
            </table>"""

        # Build watched products section
        watched_html = ""
        if watched_products:
            watched_rows = ""
            for product in watched_products:
                name = product.get("name", "")
                lowest_price = product.get("lowest_price", "N/A")
                store_name = product.get("store_name", "")
                watched_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {lowest_price} kr</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {store_name}</td>
                </tr>"""

            watched_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Dina bevakade produkter</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Lagsta pris</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                    </tr>
                </thead>
                <tbody>{watched_rows}</tbody>
            </table>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Veckans prisoversikt</h2>
            <p>Har ar en sammanfattning av priser och erbjudanden denna vecka.</p>
            {deals_html}
            {watched_html}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning.
            </p>
        </body>
        </html>
        """

    async def _send_email(self, to_email: str, subject: str, html_body: str) -> bool:
        """Send email via Resend API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.RESEND_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self.from_email,
                        "to": [to_email],
                        "subject": subject,
                        "html": html_body,
                    },
                    timeout=30.0,
                )

                if response.status_code == 200:
                    logger.info(f"Email sent successfully to {to_email}")
                    return True
                else:
                    logger.error(f"Failed to send email: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False
