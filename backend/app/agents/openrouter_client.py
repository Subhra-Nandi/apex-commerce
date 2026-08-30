"""
OPENROUTER PROVIDER (fallback).

Two jobs:
  1. Discover which models on OpenRouter are FREE right now, so we never depend
     on a hardcoded model name that gets retired.
  2. Send a prompt to a chosen model and return its raw JSON text.

Free models usually do not support strict structured output, so we describe the
required JSON shape inside the prompt and let app/agents/llm_router.py validate
the reply with Pydantic.
"""

import json
from typing import Any, Type

import requests
from pydantic import BaseModel

from app.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MAX_CANDIDATES,
    OPENROUTER_MODELS,
)

# Model families that tend to follow JSON instructions well, in rough preference
# order. This only affects ORDERING - any free model may still be used.
_PREFERRED_HINTS = (
    "deepseek",
    "qwen",
    "llama-3.3",
    "llama-3.1",
    "mistral",
    "gemma",
    "glm",
    "kimi",
    "nemotron",
    "phi",
)

_TIMEOUT_SECONDS = 90

# Cached so we only ask OpenRouter for its catalogue once per server run.
_cached_free_models: list[str] | None = None


def is_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/apex-commerce",
        "X-Title": "APEX-Commerce",
    }


def _is_free(pricing: dict[str, Any]) -> bool:
    """OpenRouter reports prices as strings. Free means both sides are zero."""
    try:
        return (
            float(pricing.get("prompt", "1")) == 0.0
            and float(pricing.get("completion", "1")) == 0.0
        )
    except (TypeError, ValueError):
        return False


def _handles_text(architecture: dict[str, Any]) -> bool:
    """Skip image-only / audio-only models."""
    outputs = architecture.get("output_modalities")
    if not outputs:
        return True  # Field absent on older entries; assume text.
    return "text" in outputs


def _score(model_id: str) -> int:
    lowered = model_id.lower()
    for index, hint in enumerate(_PREFERRED_HINTS):
        if hint in lowered:
            return index
    return len(_PREFERRED_HINTS)


def discover_free_models(force_refresh: bool = False) -> list[str]:
    """
    Ask OpenRouter which models are currently free, newest catalogue every run.
    A manual OPENROUTER_MODELS override always wins.
    """
    global _cached_free_models

    manual = [m.strip() for m in OPENROUTER_MODELS.split(",") if m.strip()]
    if manual:
        return manual

    if _cached_free_models is not None and not force_refresh:
        return _cached_free_models

    if not OPENROUTER_API_KEY:
        return []

    response = requests.get(
        f"{OPENROUTER_BASE_URL}/models", headers=_headers(), timeout=30
    )
    response.raise_for_status()
    entries = response.json().get("data", [])

    candidates: list[tuple[int, int, str]] = []
    for entry in entries:
        model_id = entry.get("id", "")
        if not model_id:
            continue
        if not _is_free(entry.get("pricing", {}) or {}):
            continue
        if not _handles_text(entry.get("architecture", {}) or {}):
            continue
        context_length = int(entry.get("context_length") or 0)
        candidates.append((_score(model_id), -context_length, model_id))

    candidates.sort()
    _cached_free_models = [model_id for _, _, model_id in candidates]
    return _cached_free_models


def candidate_models() -> list[str]:
    """The shortlist we will actually attempt, capped so requests stay snappy."""
    return discover_free_models()[:OPENROUTER_MAX_CANDIDATES]


def _build_system_instruction(system_instruction: str, schema: Type[BaseModel]) -> str:
    """Free models need the schema spelled out in words, not as an API parameter."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        f"{system_instruction}\n\n"
        "OUTPUT FORMAT - THIS IS MANDATORY:\n"
        "Reply with a single raw JSON object and NOTHING else. No markdown code "
        "fences, no explanation before or after, no comments. The object must "
        "validate against this JSON Schema:\n"
        f"{schema_json}"
    )


def generate_json(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Type[BaseModel],
    temperature: float = 0.2,
) -> str:
    """Call one specific OpenRouter model and return its raw reply text."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Add it to backend/.env - create a free "
            "key at https://openrouter.ai/keys"
        )

    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": _build_system_instruction(system_instruction, schema),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        url, headers=_headers(), json=payload, timeout=_TIMEOUT_SECONDS
    )

    # Some free models reject the response_format hint. Retry once without it.
    if response.status_code >= 400 and "response_format" in response.text:
        payload.pop("response_format", None)
        response = requests.post(
            url, headers=_headers(), json=payload, timeout=_TIMEOUT_SECONDS
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:400]}"
        )

    data = response.json()

    # OpenRouter can report errors inside a 200 response.
    if isinstance(data.get("error"), dict):
        raise RuntimeError(f"OpenRouter error: {data['error'].get('message')}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices.")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()

    # A few reasoning models leave 'content' empty and put text in 'reasoning'.
    if not content:
        content = (message.get("reasoning") or "").strip()

    if not content:
        raise RuntimeError("OpenRouter returned an empty message.")

    return content