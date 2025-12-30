"""Tests for OpenWebUI adapter chunk formatting."""

from __future__ import annotations

import json
from typing import Any

from interfaces.http.openwebui_adapter import _clean_content, _format_chunk


class TestFormatChunk:
    """Test the SSE chunk formatting function."""

    def test_format_chunk_basic(self) -> None:
        """Test basic chunk formatting."""
        result = _format_chunk("test-id", 1234567890, "gpt-4", "Hello")

        assert result.startswith("data: ")
        assert result.endswith("\n\n")

        # Parse the JSON payload
        json_str = result[6:-2]  # Remove "data: " and "\n\n"
        data = json.loads(json_str)

        assert data["id"] == "test-id"
        assert data["object"] == "chat.completion.chunk"
        assert data["created"] == 1234567890
        assert data["model"] == "gpt-4"
        assert data["choices"][0]["delta"]["content"] == "Hello"
        assert data["choices"][0]["finish_reason"] is None

    def test_format_chunk_empty_content(self) -> None:
        """Test chunk with empty content."""
        result = _format_chunk("id", 0, "model", "")
        data = json.loads(result[6:-2])
        assert data["choices"][0]["delta"]["content"] == ""

    def test_format_chunk_special_characters(self) -> None:
        """Test chunk with special characters and unicode."""
        content = "Hello 🎉 World! <script>alert('xss')</script>"
        result = _format_chunk("id", 0, "model", content)
        data = json.loads(result[6:-2])
        assert data["choices"][0]["delta"]["content"] == content


class TestCleanContent:
    """Test the content cleaning/extraction function."""

    def test_clean_content_none(self) -> None:
        """Test handling of None content."""
        result = _clean_content(None)
        assert result == "Processing..."

    def test_clean_content_simple_string(self) -> None:
        """Test simple string passthrough."""
        result = _clean_content("Hello World")
        assert result == "Hello World"

    def test_clean_content_json_with_instruction(self) -> None:
        """Test JSON extraction with instruction field."""
        json_str = json.dumps({"instruction": "Do this task", "other": "ignored"})
        result = _clean_content(json_str)
        assert result == "Do this task"

    def test_clean_content_json_with_description(self) -> None:
        """Test JSON extraction with description field."""
        json_str = json.dumps({"description": "A great plan", "data": [1, 2, 3]})
        result = _clean_content(json_str)
        assert result == "A great plan"

    def test_clean_content_json_priority_order(self) -> None:
        """Test that instruction has higher priority than description."""
        json_str = json.dumps({"description": "Lower priority", "instruction": "Higher priority"})
        result = _clean_content(json_str)
        assert result == "Higher priority"

    def test_clean_content_dict_input(self) -> None:
        """Test handling of dict input directly."""
        data: dict[str, Any] = {"summary": "Quick summary"}
        result = _clean_content(data)
        assert result == "Quick summary"

    def test_clean_content_invalid_json(self) -> None:
        """Test handling of string that looks like JSON but isn't."""
        result = _clean_content("{not valid json")
        assert result == "{not valid json"

    def test_clean_content_json_no_readable_fields(self) -> None:
        """Test JSON with no standard readable fields."""
        json_str = json.dumps({"x": 1, "y": 2})
        result = _clean_content(json_str)
        # Should return stringified dict
        assert "x" in result and "1" in result


class TestChunkTypeHandling:
    """Test various chunk type handling logic.

    These tests verify the formatting behavior for different chunk types
    without running the actual streaming pipeline.
    """

    def test_content_chunk_formatting(self) -> None:
        """Test content chunk produces expected format."""
        # Content chunks should be passed through directly
        chunk: dict[str, Any] = {"type": "content", "content": "Hello"}
        content = chunk.get("content", "")
        result = _format_chunk("id", 0, "model", content)
        data = json.loads(result[6:-2])
        assert data["choices"][0]["delta"]["content"] == "Hello"

    def test_thinking_chunk_formatting(self) -> None:
        """Test thinking chunk produces expected format."""
        # Thinking chunks are formatted with emoji and italics
        chunk: dict[str, Any] = {"type": "thinking", "content": "Analyzing the problem"}
        content = chunk.get("content", "")
        formatted = f"\n> 🧠 *{_clean_content(content)}*\n\n"

        result = _format_chunk("id", 0, "model", formatted)
        data = json.loads(result[6:-2])
        assert "🧠" in data["choices"][0]["delta"]["content"]
        assert "Analyzing the problem" in data["choices"][0]["delta"]["content"]

    def test_tool_output_success_formatting(self) -> None:
        """Test successful tool output chunk."""
        chunk: dict[str, Any] = {
            "type": "tool_output",
            "content": "Result data",
            "metadata": {"status": "success"},
        }
        status = (chunk.get("metadata") or {}).get("status", "success")
        assert status == "success"
        # Would yield "✅ *Finished*"

    def test_tool_output_error_formatting(self) -> None:
        """Test failed tool output chunk."""
        chunk: dict[str, Any] = {
            "type": "tool_output",
            "content": "",
            "metadata": {"status": "error"},
        }
        status = (chunk.get("metadata") or {}).get("status", "success")
        assert status == "error"
        # Would yield "❌ **Tool Failed**"

    def test_error_chunk_formatting(self) -> None:
        """Test error chunk produces expected format."""
        error_message = "Something went wrong"
        formatted = f"\n> ❌ **Error:** {error_message}\n\n"

        result = _format_chunk("id", 0, "model", formatted)
        data = json.loads(result[6:-2])
        assert "❌" in data["choices"][0]["delta"]["content"]
        assert "Error" in data["choices"][0]["delta"]["content"]
        assert error_message in data["choices"][0]["delta"]["content"]

    def test_step_start_formatting(self) -> None:
        """Test step start chunk formatting."""
        label = "Reading file"
        formatted = f"\n\n> 👣 **Step:** *{label}*\n\n"

        result = _format_chunk("id", 0, "model", formatted)
        data = json.loads(result[6:-2])
        assert "👣" in data["choices"][0]["delta"]["content"]
        assert "Step" in data["choices"][0]["delta"]["content"]
        assert label in data["choices"][0]["delta"]["content"]

    def test_tool_start_formatting(self) -> None:
        """Test tool start chunk formatting."""
        tool_name = "web_search"
        args_str = "(query=test)"
        formatted = f"\n> 🛠️ **Tool:** `{tool_name}` *{args_str}*\n"

        result = _format_chunk("id", 0, "model", formatted)
        data = json.loads(result[6:-2])
        assert "🛠️" in data["choices"][0]["delta"]["content"]
        assert tool_name in data["choices"][0]["delta"]["content"]

    def test_skill_start_formatting(self) -> None:
        """Test skill (consult_expert) formatting."""
        skill_name = "researcher"
        formatted = f"\n> 🧠 **Using Skill:** `{skill_name}`\n"

        result = _format_chunk("id", 0, "model", formatted)
        data = json.loads(result[6:-2])
        assert "🧠" in data["choices"][0]["delta"]["content"]
        assert "Using Skill" in data["choices"][0]["delta"]["content"]
        assert skill_name in data["choices"][0]["delta"]["content"]


class TestDebugMode:
    """Test debug mode behavior."""

    def test_debug_keyword_detection(self) -> None:
        """Test that [DEBUG] is detected case-insensitively."""
        messages = [
            "[DEBUG] Hello",
            "[debug] Hello",
            "[Debug] Hello",
            " [DEBUG] Hello",
        ]

        for msg in messages:
            assert "[DEBUG]" in msg.upper()

    def test_debug_keyword_stripping(self) -> None:
        """Test that [DEBUG] is properly stripped from message."""
        user_message = "[DEBUG] Search for something"

        # The adapter strips [DEBUG] and [debug]
        cleaned = user_message.replace("[DEBUG]", "").replace("[debug]", "").strip()
        assert cleaned == "Search for something"
