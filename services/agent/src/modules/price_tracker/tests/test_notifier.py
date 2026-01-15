"""Tests for price notifier module."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from modules.price_tracker.notifier import PriceNotifier


class TestPriceNotifier:
    """Tests for PriceNotifier class."""

    def test_build_alert_html_contains_expected_content(self) -> None:
        """Test _build_alert_html generates valid HTML with all fields."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        html = notifier._build_alert_html(
            product_name="Mjölk Arla Standard 3%",
            store_name="ICA Maxi",
            current_price=Decimal("19.90"),
            target_price=Decimal("25.00"),
            offer_type="stammispris",
            offer_details="Köp 2 betala för 1",
            product_url="https://www.ica.se/handla/produkt/test-123",
        )

        # Check basic structure
        assert "<!DOCTYPE html>" in html
        assert '<html lang="sv">' in html
        assert "</html>" in html

        # Check content elements
        assert "Prisvarning!" in html
        assert "Mjölk Arla Standard 3%" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "25.00 kr" in html  # Target price
        assert "stammispris" in html
        assert "Köp 2 betala för 1" in html
        assert "https://www.ica.se/handla/produkt/test-123" in html
        assert "Se produkten" in html  # Link button

    def test_build_alert_html_without_optional_fields(self) -> None:
        """Test _build_alert_html without target price and offer."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        html = notifier._build_alert_html(
            product_name="Smör Bregott",
            store_name="Willys",
            current_price=Decimal("29.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url=None,
        )

        # Should still have basic content
        assert "Smör Bregott" in html
        assert "Willys" in html
        assert "29.90 kr" in html

        # Should NOT have optional fields
        assert "Ditt malpris:" not in html
        assert "Erbjudande:" not in html
        assert "Se produkten" not in html

    def test_build_alert_html_with_offer_but_no_details(self) -> None:
        """Test _build_alert_html with offer type but no details."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        html = notifier._build_alert_html(
            product_name="Yoghurt",
            store_name="Coop",
            current_price=Decimal("12.50"),
            target_price=None,
            offer_type="extrapris",
            offer_details=None,
            product_url=None,
        )

        assert "extrapris" in html
        # Offer type should be in badge
        assert 'style="background: #22c55e' in html

    def test_build_summary_html_handles_empty_lists(self) -> None:
        """Test _build_summary_html with empty deals and watched products."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        html = notifier._build_summary_html(deals=[], watched_products=[])

        # Should have basic structure
        assert "<!DOCTYPE html>" in html
        assert "Veckans prisoversikt" in html
        assert "Har ar en sammanfattning" in html

        # Should NOT have tables or sections for empty data
        assert "Aktuella erbjudanden" not in html
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_deals(self) -> None:
        """Test _build_summary_html with deals."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjölk Arla",
                "store_name": "ICA Maxi",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "stammispris",
            },
            {
                "product_name": "Smör Bregott",
                "store_name": "Willys",
                "offer_price_sek": Decimal("29.90"),
                "offer_type": "kampanj",
            },
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should have deals section
        assert "Aktuella erbjudanden" in html
        assert "Mjölk Arla" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "stammispris" in html
        assert "Smör Bregott" in html
        assert "Willys" in html
        assert "kampanj" in html

        # Should NOT have watched products section
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_watched_products(self) -> None:
        """Test _build_summary_html with watched products."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": "Mjölk Arla Standard 3%",
                "lowest_price": Decimal("19.90"),
                "store_name": "ICA Maxi",
            },
            {
                "name": "Smör Bregott Original",
                "lowest_price": Decimal("29.90"),
                "store_name": "Coop",
            },
        ]

        html = notifier._build_summary_html(deals=[], watched_products=watched)

        # Should have watched products section
        assert "Dina bevakade produkter" in html
        assert "Mjölk Arla Standard 3%" in html
        assert "19.90 kr" in html
        assert "ICA Maxi" in html
        assert "Smör Bregott Original" in html
        assert "Coop" in html

        # Should NOT have deals section
        assert "Aktuella erbjudanden" not in html

    def test_build_summary_html_limits_deals_to_top_10(self) -> None:
        """Test _build_summary_html limits deals to top 10."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": f"Product {i}",
                "store_name": "Store",
                "offer_price_sek": Decimal("10.00"),
                "offer_type": "kampanj",
            }
            for i in range(20)
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should contain first 10 products
        for i in range(10):
            assert f"Product {i}" in html

        # Should NOT contain products beyond 10
        for i in range(10, 20):
            assert f"Product {i}" not in html

    @pytest.mark.asyncio
    async def test_send_email_success(self) -> None:
        """Test _send_email with successful API response."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await notifier._send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_body="<p>Test body</p>",
            )

            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args

            # Verify API call parameters
            assert call_args[0][0] == "https://api.resend.com/emails"
            assert call_args[1]["headers"]["Authorization"] == "Bearer test_key"
            assert call_args[1]["json"]["to"] == ["user@example.com"]
            assert call_args[1]["json"]["subject"] == "Test Subject"
            assert call_args[1]["json"]["html"] == "<p>Test body</p>"
            assert call_args[1]["json"]["from"] == "noreply@test.com"

    @pytest.mark.asyncio
    async def test_send_email_failure(self) -> None:
        """Test _send_email with failed API response."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await notifier._send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_body="<p>Test body</p>",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_send_email_exception(self) -> None:
        """Test _send_email handles exceptions gracefully."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        with patch("httpx.AsyncClient.post", side_effect=Exception("Network error")):
            result = await notifier._send_email(
                to_email="user@example.com",
                subject="Test Subject",
                html_body="<p>Test body</p>",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_send_price_alert(self) -> None:
        """Test send_price_alert calls _send_email with correct parameters."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        with patch.object(notifier, "_send_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            result = await notifier.send_price_alert(
                to_email="user@example.com",
                product_name="Mjölk Arla",
                store_name="ICA Maxi",
                current_price=Decimal("19.90"),
                target_price=Decimal("25.00"),
                offer_type="stammispris",
                offer_details="Köp 2 betala för 1",
                product_url="https://www.ica.se/test",
            )

            assert result is True
            mock_send.assert_called_once()
            call_args = mock_send.call_args

            assert call_args[0][0] == "user@example.com"
            assert "Prisvarning: Mjölk Arla hos ICA Maxi" in call_args[0][1]
            assert "Mjölk Arla" in call_args[0][2]

    @pytest.mark.asyncio
    async def test_send_weekly_summary(self) -> None:
        """Test send_weekly_summary calls _send_email with correct parameters."""
        notifier = PriceNotifier(api_key="test_key", from_email="noreply@test.com")

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjölk",
                "store_name": "ICA",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "kampanj",
            }
        ]
        watched: list[dict[str, str | Decimal | None]] = [
            {"name": "Smör", "lowest_price": Decimal("29.90"), "store_name": "Coop"}
        ]

        with patch.object(notifier, "_send_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            result = await notifier.send_weekly_summary(
                to_email="user@example.com",
                deals=deals,
                watched_products=watched,
            )

            assert result is True
            mock_send.assert_called_once()
            call_args = mock_send.call_args

            assert call_args[0][0] == "user@example.com"
            assert "Veckans prisoversikt" in call_args[0][1]
            assert "Mjölk" in call_args[0][2]
            assert "Smör" in call_args[0][2]
