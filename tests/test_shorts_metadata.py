"""Tests for generate_shorts_metadata LLM title/description generation."""

from unittest.mock import MagicMock, patch

from app.services.text_processor import generate_shorts_metadata


class TestGenerateShortsMetadata:
    """Test LLM-based YouTube metadata generation with fallback."""

    @patch("app.services.text_processor.get_gemini_text")
    def test_successful_generation(self, mock_gemini):
        """LLM returns valid JSON — use it."""
        mock_response = MagicMock()
        mock_response.text = '{"title": "👻【怖い話】深夜のマンションで…", "description": "友人と集まった夜…\\n#怪談 #怖い話 #心霊 #ホラー #Shorts"}'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_gemini.return_value = mock_client

        result = generate_shorts_metadata("破片", "本文テスト", "たちさん")

        assert "title" in result
        assert "description" in result
        assert "👻" in result["title"]
        assert "#怪談" in result["description"]

    @patch("app.services.text_processor.get_gemini_text")
    def test_fallback_on_invalid_json(self, mock_gemini):
        """LLM returns garbage — fall back to template."""
        mock_response = MagicMock()
        mock_response.text = "これはJSONではありません"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_gemini.return_value = mock_client

        result = generate_shorts_metadata("破片", "本文テスト", "たちさん")

        assert result["title"] == "👻【怖い話】破片"
        assert "#Shorts" in result["description"]

    @patch("app.services.text_processor.get_gemini_text")
    def test_fallback_on_exception(self, mock_gemini):
        """LLM call throws — fall back to template."""
        mock_gemini.side_effect = RuntimeError("API error")

        result = generate_shorts_metadata("破片", "本文テスト", "たちさん")

        assert result["title"] == "👻【怖い話】破片"
        assert "#Shorts" in result["description"]

    @patch("app.services.text_processor.get_gemini_text")
    def test_strips_markdown_code_blocks(self, mock_gemini):
        """LLM wraps JSON in ```json blocks — strip them."""
        mock_response = MagicMock()
        mock_response.text = '```json\n{"title": "😱【怖い話】テスト", "description": "テスト説明\\n#怪談 #Shorts"}\n```'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_gemini.return_value = mock_client

        result = generate_shorts_metadata("テスト", "本文", "作者")

        assert result["title"] == "😱【怖い話】テスト"

    @patch("app.services.text_processor.get_gemini_text")
    def test_fallback_on_empty_title(self, mock_gemini):
        """LLM returns JSON with empty title — fall back."""
        mock_response = MagicMock()
        mock_response.text = '{"title": "", "description": "some desc"}'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_gemini.return_value = mock_client

        result = generate_shorts_metadata("破片", "本文", "たちさん")

        assert result["title"] == "👻【怖い話】破片"
