"""Tests for the IntentClassifier and structured intent classification."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.routing.intent import IntentClassification, IntentClassifier


class TestIntentClassification:
    """Test IntentClassification Pydantic model."""

    def test_valid_chat_classification(self) -> None:
        """Test valid chat classification."""
        result = IntentClassification(
            route="chat",
            confidence=0.95,
            detected_skill=None,
            reasoning="Simple greeting detected",
        )
        assert result.route == "chat"
        assert result.confidence == 0.95
        assert result.detected_skill is None

    def test_valid_agentic_classification(self) -> None:
        """Test valid agentic classification."""
        result = IntentClassification(
            route="agentic",
            confidence=0.85,
            detected_skill=None,
            reasoning="Research request detected",
        )
        assert result.route == "agentic"
        assert result.confidence == 0.85

    def test_valid_skill_classification(self) -> None:
        """Test valid skill classification."""
        result = IntentClassification(
            route="skill",
            confidence=1.0,
            detected_skill="researcher",
            reasoning="Explicit slash command",
        )
        assert result.route == "skill"
        assert result.detected_skill == "researcher"

    def test_confidence_bounds(self) -> None:
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValueError):
            IntentClassification(
                route="chat",
                confidence=1.5,  # Invalid
                reasoning="test",
            )

        with pytest.raises(ValueError):
            IntentClassification(
                route="chat",
                confidence=-0.1,  # Invalid
                reasoning="test",
            )


class TestIntentClassifier:
    """Test IntentClassifier behavior."""

    @pytest.mark.asyncio
    async def test_slash_command_detection(self) -> None:
        """Test that slash commands are detected without LLM call."""
        mock_llm = AsyncMock()
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("/researcher Find Python docs")

        assert result.route == "skill"
        assert result.detected_skill == "researcher"
        assert result.confidence == 1.0
        mock_llm.generate.assert_not_called()  # No LLM call for slash commands

    @pytest.mark.asyncio
    async def test_llm_classification_chat(self) -> None:
        """Test LLM classification for chat intent."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            return_value='{"route": "chat", "confidence": 0.9, "reasoning": "greeting"}'
        )
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("Hello, how are you?")

        assert result.route == "chat"
        assert result.confidence == 0.9
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_classification_agentic(self) -> None:
        """Test LLM classification for agentic intent."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            return_value='{"route": "agentic", "confidence": 0.95, "reasoning": "research"}'
        )
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("Research the latest Python features")

        assert result.route == "agentic"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self) -> None:
        """Test fallback to agentic when LLM fails."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=Exception("API Error"))
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("What time is it?")

        # Should default to agentic on error
        assert result.route == "agentic"
        assert result.confidence == 0.5

    @pytest.mark.asyncio
    async def test_parse_malformed_json(self) -> None:
        """Test parsing when LLM returns non-JSON."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value="This should be agentic because...")
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("Search for documentation")

        # Should extract from keywords
        assert result.route == "agentic"

    @pytest.mark.asyncio
    async def test_parse_json_in_text(self) -> None:
        """Test extraction of JSON embedded in text."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(
            return_value='Here is the result: {"route": "chat", "confidence": 0.8, '
            '"detected_skill": null, "reasoning": "test"}'
        )
        classifier = IntentClassifier(mock_llm)

        result = await classifier.classify("Hi")

        assert result.route == "chat"
        assert result.confidence == 0.8
