"""
PROVIDER-AGNOSTIC STRUCTURED LLM CALLS.

Order of operations:
  1. Try Gemini, retrying with exponential backoff on transient errors
     (503 UNAVAILABLE, 429 rate limit, 5xx).
  2. If Gemini is exhausted or unconfigured, walk the list of currently-free
     OpenRouter models until one returns valid JSON.
  3. If everything fails, raise a single clear error naming every attempt.

Whichever provider answers, the reply is validated against a Pydantic schema
before it leaves this module. Downstream code can therefore treat LLM output as
strongly typed - and the deterministic enclave validates it AGAIN for money
safety. The provider swap changes nothing about our safety guarantees.
"""

import json
import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents import gemini_client, openrouter_client
from app.config import GEMINI_MAX_ATTEMPTS

T = TypeVar("T", bound=BaseModel)

# Substrings that mean "busy, try again" rather than "your request was wrong".
_TRANSIENT_MARKERS = (
    "503",
    "502",
    "504",
    "500",
    "429",
    "unavailable",
    "resource_exhausted",
    "overloaded",
    "high demand",
    "rate limit",
    "timeout",
    "timed out",
    "deadline",
)


def _is_transient(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _strip_code_fence(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_json_object(text: str) -> str:
    """
    Pull the outermost {...} out of a reply, using balanced-brace counting so a
    chatty model that adds a sentence before the JSON does not break us.
    """
    cleaned = _strip_code_fence(text)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in the model reply.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : index + 1]

    raise ValueError("Unbalanced JSON object in the model reply.")


def _validate(raw_text: str, schema: Type[T]) -> T:
    return schema.model_validate(json.loads(_extract_json_object(raw_text)))


def generate_structured(
    *,
    system_instruction: str,
    prompt: str,
    schema: Type[T],
    temperature: float = 0.2,
    trace: list[dict[str, Any]] | None = None,
) -> T:
    """
    Get a schema-valid object from whichever provider is healthy.
    Pass a list as `trace` to record which provider and model actually answered.
    """
    failures: list[str] = []

    def note(provider: str, model: str, status: str, detail: str = "") -> None:
        if trace is not None:
            trace.append(
                {
                    "provider": provider,
                    "model": model,
                    "status": status,
                    "detail": detail[:300],
                }
            )

    # ---------- 1. Primary: Gemini, with backoff ----------
    if gemini_client.is_configured():
        for attempt in range(1, max(GEMINI_MAX_ATTEMPTS, 1) + 1):
            try:
                raw = gemini_client.generate_json(
                    system_instruction=system_instruction,
                    prompt=prompt,
                    schema=schema,
                    temperature=temperature,
                )
                result = _validate(raw, schema)
                note("gemini", gemini_client.model_name(), "success")
                return result
            except Exception as error:  # noqa: BLE001 - we classify below
                failures.append(
                    f"gemini/{gemini_client.model_name()} attempt {attempt}: {error}"
                )
                transient = _is_transient(error)
                note(
                    "gemini",
                    gemini_client.model_name(),
                    "transient_error" if transient else "error",
                    str(error),
                )
                if not transient:
                    break  # A bad request will not fix itself; fail over now.
                if attempt < max(GEMINI_MAX_ATTEMPTS, 1):
                    time.sleep(1.5 * (2 ** (attempt - 1)))
    else:
        note("gemini", "-", "skipped", "GEMINI_API_KEY not set")

    # ---------- 2. Fallback: OpenRouter free models ----------
    if openrouter_client.is_configured():
        try:
            candidates = openrouter_client.candidate_models()
        except Exception as error:  # noqa: BLE001
            candidates = []
            failures.append(f"openrouter model discovery: {error}")
            note("openrouter", "-", "discovery_failed", str(error))

        if not candidates:
            note("openrouter", "-", "no_free_models_found")

        for model in candidates:
            try:
                raw = openrouter_client.generate_json(
                    model=model,
                    system_instruction=system_instruction,
                    prompt=prompt,
                    schema=schema,
                    temperature=temperature,
                )
                result = _validate(raw, schema)
                note("openrouter", model, "success")
                return result
            except (ValidationError, ValueError, json.JSONDecodeError) as error:
                failures.append(f"openrouter/{model} returned unusable JSON: {error}")
                note("openrouter", model, "bad_json", str(error))
            except Exception as error:  # noqa: BLE001
                failures.append(f"openrouter/{model}: {error}")
                note("openrouter", model, "error", str(error))
    else:
        note("openrouter", "-", "skipped", "OPENROUTER_API_KEY not set")

    raise RuntimeError(
        "All LLM providers failed. Attempts:\n- " + "\n- ".join(failures)
    )