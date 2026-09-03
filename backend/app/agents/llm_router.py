"""
PROVIDER-AGNOSTIC STRUCTURED LLM CALLS.

Three providers, tried in order. config.LLM_PRIMARY_PROVIDER decides who leads and
defaults to "gorouter"; everyone else keeps their relative order behind the leader.

  1. gorouter.app - THE PRIMARY BRAIN, running Claude Opus 5. The pinned model
     (claude-opus-5) goes first and is retried with exponential backoff on transient
     errors (429, 5xx, timeouts). Then the understudies from GOROUTER_MODELS
     (claude-opus-5-thinking) get one shot each. Billing here is PER CALL, so retry
     budgets are a spending decision: GOROUTER_MAX_ATTEMPTS defaults to 2, and only
     the pinned model is ever retried.
  2. OpenRouter - optional middle fallback. Unset OPENROUTER_API_KEY and it is
     skipped silently. Its pinned paid model goes first, then manual extras, then
     whatever models OpenRouter reports as free right now.
  3. Gemini - optional last resort. Unset GEMINI_API_KEY and it is skipped too.
  4. If everything fails, raise a single clear error naming every attempt. The route
     layer turns that one error into an honest 503 (see llm_guard.py).

An error that will not fix itself is NEVER retried: an out-of-credit answer (a
gorouter insufficient_user_quota message, or an OpenRouter HTTP 402), a bad API key,
or an unknown model id. On gorouter that restraint is money, not just demo time -
every retry is another 0.3 credits.

Whichever provider answers, the reply is validated against a Pydantic schema before
it leaves this module. Downstream code can therefore treat LLM output as strongly
typed - and the deterministic enclave validates it AGAIN for money safety. Swapping
providers changes nothing about our safety guarantees.
"""

import json
import time
from typing import Any, Callable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents import gemini_client, gorouter_client, openrouter_client
from app.config import (
    GEMINI_MAX_ATTEMPTS,
    GOROUTER_MAX_ATTEMPTS,
    LLM_PRIMARY_PROVIDER,
    OPENROUTER_MAX_ATTEMPTS,
)

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

# Substrings that mean "retrying will fail identically". Checked FIRST, because
# some of these messages also contain a number that looks transient.
#
# TWO TRAPS WORTH KNOWING ABOUT, both real:
#
#   * "insufficient_quota" (OpenAI/OpenRouter wording) is NOT a substring of
#     "insufficient_user_quota" (gorouter's wording). Both have to be listed, or a
#     gorouter out-of-credit answer gets retried - and on per-call billing a retry
#     that cannot succeed still costs 0.3 credits.
#   * A bare "quota" marker looks tempting and would be a bug. Gemini's free-tier
#     daily limit message contains "quotaId: GenerateRequestsPerDayPerProject...",
#     which is a TRANSIENT condition that clears at midnight Pacific. Matching bare
#     "quota" would reclassify it as permanent and stop Gemini being retried. Only
#     the specific strings below are safe.
_PERMANENT_MARKERS = (
    "402",
    "insufficient credit",
    "insufficient_quota",
    "requires more credits",
    "invalid api key",
    "no auth credentials",
    "user not found",
    "not a valid model",
    "unknown model",
    "no endpoints found",
    "no allowed providers",
    # gorouter.app / New API wording. Out of credit is a message here, not a 402.
    "insufficient_user_quota",
    "quota is not enough",
    "额度不足",
    "invalid token",
    "no available channel",
    "无可用渠道",
)


def _is_permanent(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _PERMANENT_MARKERS)


def _is_transient(error: Exception) -> bool:
    if _is_permanent(error):
        return False
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


_KNOWN_PROVIDERS = ("gorouter", "openrouter", "gemini")


def provider_order() -> list[str]:
    """
    Which provider leads. One env var flips it, no code edit needed.

    LLM_PRIMARY_PROVIDER=gemini gives ["gemini", "gorouter", "openrouter"]. A typo or
    a blank value falls back to gorouter rather than crashing the API at import time.
    """
    primary = (
        LLM_PRIMARY_PROVIDER
        if LLM_PRIMARY_PROVIDER in _KNOWN_PROVIDERS
        else _KNOWN_PROVIDERS[0]
    )
    return [primary] + [name for name in _KNOWN_PROVIDERS if name != primary]


def _run_chain(
    *,
    client: Any,
    provider: str,
    max_attempts: int,
    key_hint: str,
    system_instruction: str,
    prompt: str,
    schema: Type[T],
    temperature: float,
    failures: list[str],
    note: Callable[..., None],
) -> T | None:
    """
    Walk one provider's model chain. Returns a validated object, or None if the whole
    chain fails.

    gorouter and OpenRouter are the same shape - an ordered list of model ids behind
    one OpenAI-dialect endpoint - so they share this walker. `client` only has to
    provide is_configured(), candidate_models() and generate_json(...).

    The rules, in order of how much they matter:
      * Only slot 0 (the model you deliberately pinned) is retried. Backups get one
        shot each so a demo never stalls, and on gorouter so a demo never overspends.
      * A permanent error breaks out immediately - no sleep, no second charge.
      * Unusable JSON moves to the NEXT model rather than re-billing the same one.
    """
    if not client.is_configured():
        note(provider, "-", "skipped", f"{key_hint} not set")
        return None

    try:
        candidates = client.candidate_models()
    except Exception as error:  # noqa: BLE001
        candidates = []
        failures.append(f"{provider} model discovery: {error}")
        note(provider, "-", "discovery_failed", str(error))

    if not candidates:
        note(provider, "-", "no_models_available", "no model ids are configured")
        return None

    for index, model in enumerate(candidates):
        attempts_for_model = max(max_attempts, 1) if index == 0 else 1

        for attempt in range(1, attempts_for_model + 1):
            try:
                raw = client.generate_json(
                    model=model,
                    system_instruction=system_instruction,
                    prompt=prompt,
                    schema=schema,
                    temperature=temperature,
                )
                result = _validate(raw, schema)
                note(provider, model, "success")
                return result
            except (ValidationError, ValueError, json.JSONDecodeError) as error:
                # The model answered, just not with usable JSON. Another call to the
                # same model would probably do the same, so move on.
                failures.append(f"{provider}/{model} returned unusable JSON: {error}")
                note(provider, model, "bad_json", str(error))
                break
            except Exception as error:  # noqa: BLE001 - we classify below
                failures.append(f"{provider}/{model} attempt {attempt}: {error}")
                transient = _is_transient(error)
                note(
                    provider,
                    model,
                    "transient_error" if transient else "error",
                    str(error),
                )
                if not transient:
                    break  # out of credit, bad key or bad model id: pointless.
                if attempt < attempts_for_model:
                    time.sleep(1.5 * (2 ** (attempt - 1)))

    return None


def _run_gorouter(**kwargs: Any) -> Any:
    """gorouter.app, the primary. Reads its config at call time so tests can patch."""
    return _run_chain(
        client=gorouter_client,
        provider="gorouter",
        max_attempts=GOROUTER_MAX_ATTEMPTS,
        key_hint="GOROUTER_API_KEY",
        **kwargs,
    )


def _run_openrouter(**kwargs: Any) -> Any:
    """OpenRouter, the middle fallback. Skipped entirely when its key is unset."""
    return _run_chain(
        client=openrouter_client,
        provider="openrouter",
        max_attempts=OPENROUTER_MAX_ATTEMPTS,
        key_hint="OPENROUTER_API_KEY",
        **kwargs,
    )


def _run_gemini(
    *,
    system_instruction: str,
    prompt: str,
    schema: Type[T],
    temperature: float,
    failures: list[str],
    note: Callable[..., None],
) -> T | None:
    """Gemini with backoff. The last resort - unset GEMINI_API_KEY and it is skipped."""
    if not gemini_client.is_configured():
        note("gemini", "-", "skipped", "GEMINI_API_KEY not set")
        return None

    max_attempts = max(GEMINI_MAX_ATTEMPTS, 1)
    for attempt in range(1, max_attempts + 1):
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
                break  # A bad request will not fix itself.
            if attempt < max_attempts:
                time.sleep(1.5 * (2 ** (attempt - 1)))

    return None


_RUNNERS: dict[str, Callable[..., Any]] = {
    "gorouter": _run_gorouter,
    "openrouter": _run_openrouter,
    "gemini": _run_gemini,
}


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

    for provider in provider_order():
        result = _RUNNERS[provider](
            system_instruction=system_instruction,
            prompt=prompt,
            schema=schema,
            temperature=temperature,
            failures=failures,
            note=note,
        )
        if result is not None:
            return result

    if not failures:
        failures.append(
            "No LLM provider is configured. Put GOROUTER_API_KEY in backend/.env "
            "and restart uvicorn - the .env file is read once at startup."
        )

    raise RuntimeError(
        "All LLM providers failed. Attempts:\n- " + "\n- ".join(failures)
    )
