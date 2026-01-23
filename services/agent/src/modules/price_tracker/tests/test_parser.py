"""Tests for price parser module."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.price_tracker.parser import PriceExtractionResult, PriceParser


class TestPriceExtractionResult:
    """Tests for PriceExtractionResult dataclass."""

    def test_create_with_all_fields(self) -> None:
        """Test creating result with all fields populated."""
        result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=Decimal("149.50"),
            offer_price_sek=Decimal("19.90"),
            offer_type="stammispris",
            offer_details="Köp 2 betala för 1",
            in_stock=True,
            confidence=0.95,
            pack_size=16,
            raw_response={"price": 29.90, "in_stock": True},
        )

        assert result.price_sek == Decimal("29.90")
        assert result.unit_price_sek == Decimal("149.50")
        assert result.offer_price_sek == Decimal("19.90")
        assert result.offer_type == "stammispris"
        assert result.offer_details == "Köp 2 betala för 1"
        assert result.in_stock is True
        assert result.confidence == 0.95
        assert result.pack_size == 16
        assert result.raw_response == {"price": 29.90, "in_stock": True}

    def test_create_with_none_values(self) -> None:
        """Test creating result with None values."""
        result = PriceExtractionResult(
            price_sek=None,
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=False,
            confidence=0.3,
            pack_size=None,
            raw_response={},
        )

        assert result.price_sek is None
        assert result.unit_price_sek is None
        assert result.offer_price_sek is None
        assert result.offer_type is None
        assert result.offer_details is None
        assert result.in_stock is False
        assert result.confidence == 0.3
        assert result.pack_size is None


class TestPriceParser:
    """Tests for PriceParser class."""

    def test_build_prompt_without_product_name(self) -> None:
        """Test _build_prompt method without product name."""
        parser = PriceParser()
        text = "Product page content here..."
        store_slug = "ica-maxi"
        store_hint = "Look for 'pris' field"

        prompt = parser._build_prompt(text, store_slug, store_hint, None)

        assert "ica-maxi" in prompt
        assert "Look for 'pris' field" in prompt
        assert "Product page content here..." in prompt
        assert "Product being searched:" not in prompt
        assert "Return a JSON object" in prompt

    def test_build_prompt_with_product_name(self) -> None:
        """Test _build_prompt method with product name."""
        parser = PriceParser()
        text = "Product page content here..."
        store_slug = "willys"
        store_hint = "Check kampanj section"
        product_name = "Mjölk Arla Standard 3%"

        prompt = parser._build_prompt(text, store_slug, store_hint, product_name)

        assert "willys" in prompt
        assert "Check kampanj section" in prompt
        assert "Product being searched: Mjölk Arla Standard 3%" in prompt
        assert "Product page content here..." in prompt

    def test_build_prompt_truncates_long_content(self) -> None:
        """Test that _build_prompt truncates content beyond 6000 chars."""
        parser = PriceParser()
        text = "x" * 10000  # 10k characters
        store_slug = "coop"
        store_hint = ""

        prompt = parser._build_prompt(text, store_slug, store_hint, None)

        # Content should be truncated to 6000 chars
        assert "x" * 6000 in prompt
        assert len([line for line in prompt.split("\n") if "xxxx" in line][0]) <= 6010

    def test_load_store_hints(self) -> None:
        """Test that store hints are loaded on initialization."""
        with patch("modules.price_tracker.stores.get_store_hints") as mock_hints:
            mock_hints.return_value = {
                "ica-maxi": "ICA hint",
                "willys": "Willys hint",
            }

            parser = PriceParser()

            assert parser._store_hints == {
                "ica-maxi": "ICA hint",
                "willys": "Willys hint",
            }
            mock_hints.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_model_success(self) -> None:
        """Test _extract_with_model with successful extraction."""
        parser = PriceParser()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "price": 29.90,
                            "unit_price": 149.50,
                            "offer_price": None,
                            "offer_type": None,
                            "offer_details": None,
                            "in_stock": True,
                            "confidence": 0.95,
                        }
                    )
                )
            )
        ]

        with patch("modules.price_tracker.parser.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await parser._extract_with_model("test prompt", "haiku")

            assert result.price_sek == Decimal("29.90")
            assert result.unit_price_sek == Decimal("149.50")
            assert result.offer_price_sek is None
            assert result.in_stock is True
            assert result.confidence == 0.95
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_model_strips_markdown_code_blocks(self) -> None:
        """Test _extract_with_model handles markdown code blocks."""
        parser = PriceParser()

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='```json\n{"price": 15.50, "in_stock": true, "confidence": 0.8}\n```'
                )
            )
        ]

        with patch("modules.price_tracker.parser.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await parser._extract_with_model("test prompt", "sonnet")

            assert result.price_sek == Decimal("15.50")
            assert result.in_stock is True
            assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_extract_price_uses_haiku_first(self) -> None:
        """Test that extract_price tries Haiku model first."""
        parser = PriceParser()

        mock_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.85,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_result

            result = await parser.extract_price("page content", "ica-maxi", "Mjölk")

            # Should call with Haiku model first
            mock_extract.assert_called_once()
            assert "haiku" in str(mock_extract.call_args)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_extract_price_retries_with_sonnet_on_low_confidence(self) -> None:
        """Test that extract_price retries with Sonnet if Haiku confidence is low."""
        parser = PriceParser()

        haiku_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.5,  # Below threshold
            pack_size=None,
            raw_response={},
        )

        sonnet_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.95,  # High confidence
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            mock_extract.side_effect = [haiku_result, sonnet_result]

            result = await parser.extract_price("page content", "willys")

            # Should call twice: Haiku first, then Sonnet
            assert mock_extract.call_count == 2
            assert result == sonnet_result

    @pytest.mark.asyncio
    async def test_extract_price_falls_back_to_sonnet_on_error(self) -> None:
        """Test that extract_price falls back to Sonnet if Haiku fails."""
        parser = PriceParser()

        sonnet_result = PriceExtractionResult(
            price_sek=Decimal("29.90"),
            unit_price_sek=None,
            offer_price_sek=None,
            offer_type=None,
            offer_details=None,
            in_stock=True,
            confidence=0.9,
            pack_size=None,
            raw_response={},
        )

        with patch.object(parser, "_extract_with_model", new_callable=AsyncMock) as mock_extract:
            # Haiku raises exception, Sonnet succeeds
            mock_extract.side_effect = [Exception("Haiku error"), sonnet_result]

            result = await parser.extract_price("page content", "coop")

            assert mock_extract.call_count == 2
            assert result == sonnet_result
