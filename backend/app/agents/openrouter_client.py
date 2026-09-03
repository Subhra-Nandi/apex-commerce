"""
OpenRouter client - the OPTIONAL MIDDLE FALLBACK.

The primary brain is now Claude Opus 5 on gorouter.app (see gorouter_client.py).
This file sits second in the chain and is SKIPPED ENTIRELY, in silence, whenever
OPENROUTER_API_KEY is unset - which is the normal configuration, and what makes the
dashboard header read "Fallback Gemini 2.5 Flash". Set that key and this provider
quietly inserts itself between gorouter and Gemini; nothing else needs editing.

Keep the module even when unused: it costs nothing at import time, and it is the
one provider here that can reach a *different* vendor's model if Anthropic itself
is having a bad day.

One HTTP endpoint (/chat/completions) fronts hundreds of models, so we use it in
two very different ways:

  1. THE PINNED PAID MODEL (config.OPENROUTER_PRIMARY_MODEL, e.g.
     "anthropic/claude-sonnet-4.5"). This is the model we actually want doing the
     negotiating. It is paid, so it is named explicitly and NEVER runs through the
     "is this free?" filter below - that filter would throw it away.

  2. THE FREE BACKUP CHAIN. If the paid model is unreachable, or the account runs
     out of credit mid-demo, we walk whatever models OpenRouter reports as free
     RIGHT NOW, discovered at runtime from GET /models. Hardcoded free-model ids
     go stale within weeks, which is why we never hardcode them.

Nothing in this file can move money. It returns text; app/agents/llm_router.py
validates that text against a Pydantic schema, and the deterministic policy
enclave validates the result AGAIN before Razorpay is ever called.
"""

import json
from typing import Any, Type

import requests
from pydantic import BaseModel

from app.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_FREE_FALLBACK,
    OPENROUTER_MAX_CANDIDATES,
    OPENROUTER_MODELS,
    OPENROUTER_PRIMARY_MODEL,
)

# Free models we prefer, best first. Only used to order the BACKUP chain.
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

# Upper bound on the reply length. Our schemas are a handful of order lines plus a
# sentence of reasoning, so this is generous - but setting it explicitly means a
# long-winded model gets cut off predictably and we can detect that (see the
# finish_reason check in generate_json) instead of blaming the JSON parser.
_MAX_OUTPUT_TOKENS = 4000

_cached_free_models: list[str] | None = None


def is_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def primary_model() -> str:
    """The pinned paid model, or "" if the operator blanked it out."""
    return OPENROUTER_PRIMARY_MODEL


def manual_models() -> list[str]:
    """Extra models from OPENROUTER_MODELS, tried after the pinned model."""
    return [entry.strip() for entry in OPENROUTER_MODELS.split(",") if entry.strip()]


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
    modalities = architecture.get("input_modalities") or ["text"]
    return "text" in modalities


def _score(model_id: str) -> int:
    """Lower is better. Unknown families sort last but are still usable."""
    lowered = model_id.lower()
    for index, hint in enumerate(_PREFERRED_HINTS):
        if hint in lowered:
            return index
    return len(_PREFERRED_HINTS)


def discover_free_models(force_refresh: bool = False) -> list[str]:
    """
    Ask OpenRouter which models cost nothing at this moment, best first.

    This is the BACKUP chain only. The pinned paid model does not appear here and
    must not - a paid model has a non-zero price and is filtered out by design.
    Results are cached for the life of the process; pass force_refresh=True from
    the /agent/llm-status diagnostic to re-read the live list.
    """
    global _cached_free_models

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
    """
    The exact order the router will try, best first:

        1. the pinned paid model            (OPENROUTER_PRIMARY_MODEL)
        2. any manual extras                (OPENROUTER_MODELS)
        3. whatever is free right now        (only if OPENROUTER_FREE_FALLBACK)

    Duplicates are removed, order is preserved, and slot 1 is never trimmed by
    OPENROUTER_MAX_CANDIDATES - the model you paid for always gets its turn.
    """
    ordered: list[str] = []

    def add(model_id: str) -> None:
        if model_id and model_id not in ordered:
            ordered.append(model_id)

    add(primary_model())
    for model_id in manual_models():
        add(model_id)

    if OPENROUTER_FREE_FALLBACK:
        try:
            for model_id in discover_free_models():
                add(model_id)
        except Exception:  # noqa: BLE001 - discovery is a nicety, never fatal
            pass

    limit = max(1, OPENROUTER_MAX_CANDIDATES)
    return ordered[:limit]


def describe() -> dict[str, Any]:
    """Config snapshot for /agent/llm-status. Makes no network call."""
    return {
        "configured": is_configured(),
        "base_url": OPENROUTER_BASE_URL,
        "pinned_model": primary_model() or None,
        "pinned_model_is_paid": bool(primary_model()),
        "manual_models": manual_models(),
        "free_fallback_enabled": OPENROUTER_FREE_FALLBACK,
        "max_candidates": max(1, OPENROUTER_MAX_CANDIDATES),
    }


def _build_system_instruction(system_instruction: str, schema: Type[BaseModel]) -> str:
    """
    Paste the JSON Schema into the prompt itself.

    Claude honours response_format, but several of the free backup models do not,
    so the schema goes in the text where every model can see it. Belt and braces:
    llm_router validates the reply against the same schema afterwards regardless.
    """
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
    """Call one specific model and return its raw text reply."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Add it to backend/.env - "
            "create a key at https://openrouter.ai/keys"
        )

    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": _MAX_OUTPUT_TOKENS,
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

    # Some models reject the response_format hint. Retry once without it.
    if response.status_code >= 400 and "response_format" in response.text:
        payload.pop("response_format", None)
        response = requests.post(
            url, headers=_headers(), json=payload, timeout=_TIMEOUT_SECONDS
        )

    if response.status_code == 402:
        # Out of credit. Named explicitly because this is the one failure a paid
        # primary model adds, and retrying it is pointless.
        raise RuntimeError(
            f"OpenRouter HTTP 402: insufficient credits for '{model}'. "
            f"Top up at https://openrouter.ai/credits or set "
            f"OPENROUTER_PRIMARY_MODEL to a free model. "
            f"Detail: {response.text[:200]}"
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:400]}"
        )

    data = response.json()
    if isinstance(data.get("error"), dict):
        raise RuntimeError(f"OpenRouter error: {data['error'].get('message')}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices.")

    choice = choices[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:  # some reasoning models put the answer here instead
        content = (message.get("reasoning") or "").strip()

    if not content:
        raise RuntimeError("OpenRouter returned an empty message.")

    # A reply cut off at the token limit is unparseable JSON. Say so plainly
    # rather than letting it surface as a confusing schema-validation error.
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            f"OpenRouter reply from '{model}' hit the {_MAX_OUTPUT_TOKENS}-token "
            "limit and is incomplete JSON."
        )

    return content
