"""
Shared Google Gemini client plus a helper that forces structured JSON output
matching a Pydantic schema.

The client is created lazily (on first use) so the whole API can still start
even if GEMINI_API_KEY is missing - only the agent endpoints would fail.
"""

import json
from typing import Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import GEMINI_API_KEY, GEMINI_MODEL

T = TypeVar("T", bound=BaseModel)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is missing. Add it to backend/.env - get a free key "
                "at https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ```json fences. Remove them if present."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def generate_structured(
    *,
    system_instruction: str,
    prompt: str,
    schema: Type[T],
    temperature: float = 0.2,
) -> T:
    """
    Ask Gemini for a response that conforms exactly to `schema`.
    Low temperature keeps commercial reasoning consistent rather than creative.
    """
    client = _get_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )

    # Preferred path: the SDK parses the JSON into our Pydantic model for us.
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed

    # Fallback: parse the raw text ourselves.
    raw = _strip_code_fence(response.text)
    return schema.model_validate(json.loads(raw))