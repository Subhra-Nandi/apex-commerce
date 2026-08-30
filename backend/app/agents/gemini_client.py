"""
GEMINI PROVIDER.

One job: send a prompt to Google Gemini and return the raw JSON text it replies
with. It does NOT validate or retry - app/agents/llm_router.py handles that.

The client is created lazily so the API still starts without a Gemini key.
"""

from typing import Type

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import GEMINI_API_KEY, GEMINI_MODEL

_client = None


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


def model_name() -> str:
    return GEMINI_MODEL


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


def generate_json(
    *,
    system_instruction: str,
    prompt: str,
    schema: Type[BaseModel],
    temperature: float = 0.2,
) -> str:
    """
    Ask Gemini for JSON. Gemini supports native structured output, so we hand it
    the Pydantic schema directly - the strongest guarantee available to us.
    Returns the raw response text.
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
    return response.text or ""