"""Shared API client initialization with lazy loading and caching."""

from __future__ import annotations

import os

_gemini_text = None
_gemini_image = None
_openai = None


def get_gemini_text():
    """Get Gemini client for text operations (text-to-text)."""
    global _gemini_text
    if _gemini_text is None:
        from google import genai

        api_key = (
            os.environ.get("GEMINI_API_KEY_TEXT_TO_TEXT")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        _gemini_text = genai.Client(api_key=api_key)
    return _gemini_text


def get_gemini_image():
    """Get Gemini client for image operations (text-to-image)."""
    global _gemini_image
    if _gemini_image is None:
        from google import genai

        api_key = (
            os.environ.get("GEMINI_API_KEY_TEXT_TO_IMAGE")
            or os.environ.get("GEMINI_API_KEY_TEXT_TO_TEXT")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        _gemini_image = genai.Client(api_key=api_key)
    return _gemini_image


def get_openai():
    """Get OpenAI client."""
    global _openai
    if _openai is None:
        from openai import OpenAI

        _openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    return _openai
