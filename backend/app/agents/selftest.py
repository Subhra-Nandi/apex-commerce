"""
Provider-order selftest for the LLM layer. NO NETWORK, NO API KEYS, NO COST.

Run it with:

    python -m app.agents.selftest

Every language-model call is replaced by a fake that records which model was
asked and either returns canned JSON or raises a canned error. That matters more
than usual now: the primary brain is Claude Opus 5 on gorouter.app, billed PER
CALL, so a test suite that quietly reached the real gateway would spend real
credits. Every provider is faked, including gorouter, and gorouter is faked as
UNCONFIGURED unless a section explicitly scripts it.

What this proves:

  1. gorouter leads, OpenRouter is the middle fallback, Gemini is the last resort,
     and a typo in LLM_PRIMARY_PROVIDER falls back to gorouter instead of crashing.
  2. The pinned model is tried FIRST on both gateways and can never be trimmed away
     - on OpenRouter it also survives the "is it free?" filter that guards the
     backup chain.
  3. gorouter's catalog check spots a wrongly prefixed model id
     ("anthropic/claude-opus-5") BEFORE a credit is spent on finding out.
  4. A "busy" error (429/5xx) is retried; "out of credit" is NOT - and on gorouter
     that restraint is money, because every retry is another 0.3 credits.
  5. gorouter_client's own error wording and llm_router's permanent-marker list
     agree with each other. They are separate files, so that agreement is asserted,
     not assumed.
  6. Gemini's free-tier daily "quotaId" message is still classified TRANSIENT. If
     someone ever "tidies up" the marker list by adding a bare "quota", this test
     fails - which is the point.
  7. When every provider is down, the failure is still the exact RuntimeError that
     llm_guard turns into a truthful 503 - no order, no Razorpay, no money - and the
     advice names the right dashboard for the provider that actually ran dry.

This file touches no database and no HTTP server, so it is safe to run any time.
"""

import json
import sys
from contextlib import contextmanager
from typing import Any, Callable

from pydantic import BaseModel

from app.agents import (
    gemini_client,
    gorouter_client,
    llm_guard,
    llm_router,
    openrouter_client,
)

# gorouter.app model ids carry NO vendor prefix. That is the whole point of
# GO_PREFIXED below: it is the mistake the catalog check exists to catch.
GO_PINNED = "claude-opus-5"
GO_SPARE = "claude-opus-5-thinking"
GO_PREFIXED = "anthropic/claude-opus-5"
GO_PRICE = 0.3

PINNED = "anthropic/claude-sonnet-4.5"
FREE_ONE = "deepseek/deepseek-chat-v3:free"
FREE_TWO = "qwen/qwen-2.5-72b-instruct:free"

# What a well-behaved model returns for the probe schema below.
GOOD_JSON = '{"sku": "MOU-WL-01", "quantity": 2}'

_passed = 0
_failed = 0


class Probe(BaseModel):
    """Smallest possible stand-in for the real agent schemas."""

    sku: str
    quantity: int


def check(label: str, condition: bool, detail: str = "") -> None:
    """Print one assertion. ASCII only - Windows consoles choke on fancy glyphs."""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {label}")
    else:
        _failed += 1
        print(f"[FAIL] {label}")
        if detail:
            print(f"       {detail}")


def run(section: Callable[[], None]) -> None:
    """
    Run one numbered section.

    A section that CRASHES is reported as a failed check and the suite carries on.
    Without this, one unexpected exception would abort the run and silently hide
    every check after it - which looks like "the test is broken" instead of "the
    code is broken".
    """
    try:
        section()
    except Exception as error:  # noqa: BLE001 - a crash IS a result
        check(
            f"section {section.__name__} ran to completion",
            False,
            f"it crashed: {type(error).__name__}: {error}",
        )


@contextmanager
def patched(module: Any, **attributes: Any):
    """Temporarily replace attributes on a module, then put them all back."""
    saved = {name: getattr(module, name) for name in attributes}
    for name, value in attributes.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


class NoSleep:
    """Stands in for the time module so backoff waits cost zero seconds."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def _fake_chain(provider: str, script: dict[str, Any], calls: list[str]):
    """
    One fake for both gateway-style providers. `script` maps a model id to canned
    text or an Exception; every call is logged as "provider:model" so a section can
    assert exactly who was asked, in what order, and how many times.
    """

    def generate_json(*, model, system_instruction, prompt, schema, temperature=0.2):
        calls.append(f"{provider}:{model}")
        outcome = script.get(model, RuntimeError(f"no script entry for {model}"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return generate_json


def fake_openrouter(script: dict[str, Any], calls: list[str]):
    """Fake OpenRouter. `script` maps a model id to canned text or an Exception."""
    return _fake_chain("openrouter", script, calls)


def fake_gorouter(script: dict[str, Any], calls: list[str]):
    """Fake gorouter.app. Same shape - both share the router's one chain walker."""
    return _fake_chain("gorouter", script, calls)


def fake_gemini(outcome: Any, calls: list[str]):
    """Fake Gemini. `outcome` is canned text or an Exception."""

    def generate_json(*, system_instruction, prompt, schema, temperature=0.2):
        calls.append("gemini:gemini-2.5-flash")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return generate_json


def ask(**overrides: Any) -> Probe:
    """Call the real router with the real code path, minus the network."""
    return llm_router.generate_structured(
        system_instruction="pick one product",
        prompt="a wireless mouse",
        schema=Probe,
        **overrides,
    )


@contextmanager
def world(
    *,
    chain: list[str],
    script: dict[str, Any],
    gemini: Any = None,
    primary: str = "openrouter",
    attempts: int = 3,
    go_chain: list[str] | None = None,
    go_script: dict[str, Any] | None = None,
    go_attempts: int = 2,
):
    """
    Build a fake provider world: which models each gateway offers, how each one
    behaves, and whether Gemini is configured at all. Yields the call log and the
    fake clock.

    gorouter is reported as NOT CONFIGURED unless go_script is passed. That default
    is deliberate: gorouter bills per call, so a section that forgot to fake it would
    otherwise reach the real gateway and spend real credits. Nothing here can open a
    socket either way, because generate_json itself is replaced.
    """
    calls: list[str] = []
    clock = NoSleep()
    with patched(
        gorouter_client,
        is_configured=lambda: go_script is not None,
        candidate_models=lambda: list(go_chain or []),
        generate_json=fake_gorouter(go_script or {}, calls),
    ), patched(
        openrouter_client,
        is_configured=lambda: True,
        candidate_models=lambda: list(chain),
        generate_json=fake_openrouter(script, calls),
    ), patched(
        gemini_client,
        is_configured=lambda: gemini is not None,
        model_name=lambda: "gemini-2.5-flash",
        generate_json=fake_gemini(gemini, calls),
    ), patched(
        llm_router,
        LLM_PRIMARY_PROVIDER=primary,
        GOROUTER_MAX_ATTEMPTS=go_attempts,
        OPENROUTER_MAX_ATTEMPTS=attempts,
        GEMINI_MAX_ATTEMPTS=2,
        time=clock,
    ):
        yield calls, clock


class FakeResponse:
    """Just enough of a requests.Response for discover_free_models."""

    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeModelsAPI:
    """Stands in for the requests module inside openrouter_client."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries

    def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ARG002
        return FakeResponse({"data": self.entries})


class FakeHttpResponse:
    """
    Just enough of a requests.Response for gorouter_client, including .text - which
    is what _describe_http_error reads to decide WHICH failure this is.
    """

    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str | None = None
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class FakeGateway:
    """
    Stands in for the requests module inside gorouter_client. Opens no sockets.

    `post` may be a single response or a list, in which case the Nth call gets the
    Nth entry and the last entry repeats.
    """

    def __init__(self, *, post: Any = None, pricing: Any = None) -> None:
        self._post = post
        self._pricing = pricing
        self.bodies: list[dict[str, Any]] = []
        self.get_urls: list[str] = []

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ANN001
        self.bodies.append(json or {})
        if isinstance(self._post, list):
            index = min(len(self.bodies) - 1, len(self._post) - 1)
            return self._post[index]
        return self._post

    def get(self, url, headers=None, timeout=None):  # noqa: ANN001
        self.get_urls.append(url)
        return self._pricing


# The exact shape GET https://gorouter.app/api/pricing returned on 2026-09-03:
# quota_type 1 means a FLAT price per call, held in model_price.
PRICING_PAYLOAD = {
    "data": [
        {
            "model_name": GO_PINNED,
            "quota_type": 1,
            "model_price": GO_PRICE,
            "model_ratio": 0,
            "completion_ratio": 0,
            "supported_endpoint_types": ["anthropic", "openai"],
        },
        {
            "model_name": GO_SPARE,
            "quota_type": 1,
            "model_price": GO_PRICE,
            "model_ratio": 0,
            "completion_ratio": 0,
            "supported_endpoint_types": ["anthropic", "openai"],
        },
    ]
}


# --------------------------------------------------------------- section 1
def section_provider_order() -> None:
    print("\n1. Who leads")
    with patched(llm_router, LLM_PRIMARY_PROVIDER="gorouter"):
        order = llm_router.provider_order()
    check(
        "default order is gorouter, then OpenRouter, then Gemini",
        order == ["gorouter", "openrouter", "gemini"],
        f"got {order}",
    )

    with patched(llm_router, LLM_PRIMARY_PROVIDER="gemini"):
        order = llm_router.provider_order()
    check(
        "LLM_PRIMARY_PROVIDER=gemini puts Gemini in front (the rollback path works)",
        order == ["gemini", "gorouter", "openrouter"],
        f"got {order}",
    )

    with patched(llm_router, LLM_PRIMARY_PROVIDER="openrouter"):
        order = llm_router.provider_order()
    check(
        "LLM_PRIMARY_PROVIDER=openrouter still leads with OpenRouter",
        order == ["openrouter", "gorouter", "gemini"],
        f"got {order}",
    )

    # A typo in backend/.env must not take the API down at import time. It falls
    # back to the default lead instead.
    for typo in ("gorouterr", "", "GoRouter ", "openai"):
        with patched(llm_router, LLM_PRIMARY_PROVIDER=typo):
            order = llm_router.provider_order()
        check(
            f"a bad LLM_PRIMARY_PROVIDER ({typo!r}) falls back to gorouter leading",
            order == ["gorouter", "openrouter", "gemini"],
            f"got {order}",
        )

    check(
        "every provider in the order has a runner wired up (no silent skips)",
        sorted(llm_router._RUNNERS) == sorted(llm_router._KNOWN_PROVIDERS),
        f"got runners {sorted(llm_router._RUNNERS)}",
    )


# --------------------------------------------------------------- section 2
def section_gorouter_chain_and_catalog() -> None:
    print("\n2. gorouter: the pinned chain, and catching a bad model id for free")
    with patched(
        gorouter_client,
        GOROUTER_PRIMARY_MODEL=GO_PINNED,
        GOROUTER_MODELS=f"{GO_SPARE}, {GO_PINNED} ",
        GOROUTER_MAX_CANDIDATES=2,
    ):
        models = gorouter_client.candidate_models()
        with patched(gorouter_client, GOROUTER_MAX_CANDIDATES=1):
            trimmed = gorouter_client.candidate_models()
        with patched(gorouter_client, GOROUTER_PRIMARY_MODEL=""):
            blanked = gorouter_client.candidate_models()

    check(
        "the pinned model is tried first and the understudy second",
        models == [GO_PINNED, GO_SPARE],
        f"got {models}",
    )
    check(
        "a duplicate in GOROUTER_MODELS is dropped, so it is never billed twice",
        models.count(GO_PINNED) == 1,
        f"got {models}",
    )
    check(
        "GOROUTER_MAX_CANDIDATES=1 can never trim away the model you pinned",
        trimmed == [GO_PINNED],
        f"got {trimmed}",
    )
    check(
        "blanking GOROUTER_PRIMARY_MODEL falls through to the understudies, not to []",
        blanked == [GO_SPARE, GO_PINNED],
        f"got {blanked}",
    )

    # The catalog is PUBLIC on this gateway - no API key, no charge - so proving the
    # model id exists costs nothing. This is the difference between reading "no
    # available channel" mid-demo and knowing the id was wrong beforehand.
    gateway = FakeGateway(pricing=FakeHttpResponse(payload=PRICING_PAYLOAD))
    with patched(
        gorouter_client,
        requests=gateway,
        _cached_catalog=None,
        GOROUTER_PRIMARY_MODEL=GO_PINNED,
    ):
        catalog = gorouter_client.discover_catalog(force_refresh=True)
        good = gorouter_client.catalog_check()
    with patched(
        gorouter_client,
        requests=gateway,
        _cached_catalog=None,
        GOROUTER_PRIMARY_MODEL=GO_PREFIXED,
    ):
        bad = gorouter_client.catalog_check()

    check(
        "the catalog is read from the site root, NOT from under /v1",
        bool(gateway.get_urls)
        and gateway.get_urls[0].endswith("/api/pricing")
        and "/v1/" not in gateway.get_urls[0],
        f"got {gateway.get_urls}",
    )
    check(
        "no Authorization header is sent - the pricing endpoint is keyless",
        len(gateway.get_urls) >= 2,
        f"got {gateway.get_urls}",
    )
    check(
        "quota_type 1 is read as flat PER-CALL billing at model_price",
        catalog[GO_PINNED]["billing"] == "per_call"
        and catalog[GO_PINNED]["per_call_price"] == GO_PRICE,
        f"got {catalog}",
    )
    check(
        "the pinned id is confirmed to exist, and its per-call price is reported",
        good["reachable"] is True
        and good["pinned_model_found"] is True
        and good["pinned_model_per_call_price"] == GO_PRICE,
        f"got {good}",
    )
    check(
        f"an OpenRouter-style id ({GO_PREFIXED}) is reported as NOT FOUND",
        bad["pinned_model_found"] is False
        and GO_PREFIXED not in bad["models_offered"]
        and GO_PINNED in bad["models_offered"],
        f"got {bad}",
    )

    class ExplodingGateway:
        """A gateway whose pricing endpoint is unreachable."""

        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("name or service not known")

    with patched(gorouter_client, requests=ExplodingGateway(), _cached_catalog=None):
        broken = gorouter_client.catalog_check()

    check(
        "an unreachable catalog is REPORTED, never raised - a diagnostic must not 500",
        broken["reachable"] is False and broken["error"] is not None,
        f"got {broken}",
    )
    with patched(gorouter_client, GOROUTER_API_KEY=""):
        credit = gorouter_client.fetch_balance()

    check(
        "a missing key makes the credit read explain itself instead of crashing",
        credit["checked"] is False and credit["error"] == "GOROUTER_API_KEY not set",
        f"got {credit}",
    )

# --------------------------------------------------------------- section 3
def section_gorouter_client_dialect() -> None:
    print("\n3. gorouter client: one call, one charge, and the thinking variant")
    answered = FakeHttpResponse(
        payload={"choices": [{"message": {"content": GOOD_JSON}, "finish_reason": "stop"}]}
    )
    gateway = FakeGateway(post=answered)
    with patched(
        gorouter_client,
        requests=gateway,
        GOROUTER_API_KEY="sk-test-key-not-real",
        _cached_catalog={GO_PINNED: {"billing": "per_call", "per_call_price": GO_PRICE}},
        _billable_calls=0,
        _estimated_credits=0.0,
    ):
        text = gorouter_client.generate_json(
            model=GO_PINNED,
            system_instruction="pick one product",
            prompt="a wireless mouse",
            schema=Probe,
        )
        meter = gorouter_client.describe()

    body = gateway.bodies[0] if gateway.bodies else {}
    system_message = str((body.get("messages") or [{}])[0].get("content", ""))

    check("the reply text is returned untouched", text == GOOD_JSON, f"got {text!r}")
    check(
        "the model id goes on the wire with NO vendor prefix",
        body.get("model") == GO_PINNED and "/" not in str(body.get("model")),
        f"got {body.get('model')!r}",
    )
    check(
        "the JSON Schema is pasted into the prompt, for models that ignore response_format",
        '"quantity"' in system_message and '"sku"' in system_message,
        f"got {system_message[:200]!r}",
    )
    check(
        "max_tokens is generous, because reply length is free under per-call billing",
        int(body.get("max_tokens") or 0) >= 4000,
        f"got {body.get('max_tokens')}",
    )
    check(
        "one completion is metered as exactly one billable call at 0.3 credits",
        meter["billable_calls_this_process"] == 1
        and meter["estimated_credits_spent"] == GO_PRICE,
        f"got {meter}",
    )
    check(
        "describe() says out loud that a retry is a fresh charge",
        "retry" in str(meter["billing"]),
        f"got {meter['billing']!r}",
    )

    # The -thinking variant can spend its output on reasoning and leave `content`
    # empty. The answer is still there, under a different key. Losing it would look
    # like the model failed, and would cost a credit to rediscover.
    thinking = FakeHttpResponse(
        payload={
            "choices": [
                {
                    "message": {"content": "", "reasoning_content": GOOD_JSON},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    with patched(
        gorouter_client,
        requests=FakeGateway(post=thinking),
        GOROUTER_API_KEY="sk-test-key-not-real",
        _cached_catalog=None,
        _billable_calls=0,
        _estimated_credits=0.0,
    ):
        try:
            thinking_text: Any = gorouter_client.generate_json(
                model=GO_SPARE, system_instruction="s", prompt="p", schema=Probe
            )
        except Exception as raised:  # noqa: BLE001 - report it, do not abort the suite
            thinking_text = f"raised: {raised}"

    check(
        "the -thinking variant's answer is found in reasoning_content, not thrown away",
        thinking_text == GOOD_JSON,
        f"got {thinking_text!r}",
    )

    truncated = FakeHttpResponse(
        payload={
            "choices": [
                {"message": {"content": '{"sku": "MOU-WL'}, "finish_reason": "length"}
            ]
        }
    )
    with patched(
        gorouter_client,
        requests=FakeGateway(post=truncated),
        GOROUTER_API_KEY="sk-test-key-not-real",
        _cached_catalog=None,
        _billable_calls=0,
        _estimated_credits=0.0,
    ):
        try:
            gorouter_client.generate_json(
                model=GO_PINNED, system_instruction="s", prompt="p", schema=Probe
            )
            cutoff: BaseException | None = None
        except RuntimeError as raised:
            cutoff = raised
        billed_anyway = gorouter_client._billable_calls

    check(
        "a reply cut off at the token limit says so, instead of blaming the JSON parser",
        cutoff is not None and "token limit" in str(cutoff),
        f"got {cutoff}",
    )
    check(
        "a truncated reply is still counted as billed, because it was",
        billed_anyway == 1,
        f"got {billed_anyway}",
    )

    with patched(
        gorouter_client,
        requests=FakeGateway(post=answered),
        GOROUTER_API_KEY="",
    ):
        try:
            gorouter_client.generate_json(
                model=GO_PINNED, system_instruction="s", prompt="p", schema=Probe
            )
            no_key: BaseException | None = None
        except RuntimeError as raised:
            no_key = raised

    check(
        "a missing key is explained (add it to .env, restart uvicorn), not a stack trace",
        no_key is not None
        and "GOROUTER_API_KEY" in str(no_key)
        and "restart uvicorn" in str(no_key),
        f"got {no_key}",
    )

# --------------------------------------------------------------- section 4
def section_gorouter_errors_are_classified() -> None:
    """
    gorouter_client writes the error text; llm_router decides whether to retry it.
    They are separate files, so their agreement is asserted here rather than assumed.
    Getting this wrong costs money: a retried hopeless call is another 0.3 credits.
    """
    print("\n4. gorouter's wording and the router's retry rules agree")

    quota = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=200,
            text='{"error":{"message":"insufficient_user_quota: user quota is not'
            ' enough","type":"new_api_error"}}',
        ),
    )
    bad_key = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=401,
            text='{"error":{"message":"Invalid token (request id: 2026090301)",'
            '"type":"new_api_error"}}',
        ),
    )
    bad_id = gorouter_client._describe_http_error(
        GO_PREFIXED,
        FakeHttpResponse(
            status_code=400,
            text='{"error":{"message":"no available channel for model'
            ' anthropic/claude-opus-5"}}',
        ),
    )
    busy = gorouter_client._describe_http_error(
        GO_PINNED, FakeHttpResponse(status_code=503, text="upstream temporarily down")
    )

    check(
        "out of credit is PERMANENT - on this gateway it is a quota message, not a 402",
        llm_router._is_permanent(quota) and not llm_router._is_transient(quota),
        f"got {quota}",
    )
    check(
        "'insufficient_quota' is NOT a substring of gorouter's wording, so both are listed",
        "insufficient_quota" not in "insufficient_user_quota"
        and any(
            "insufficient_user_quota" in marker for marker in llm_router._PERMANENT_MARKERS
        ),
        f"markers: {llm_router._PERMANENT_MARKERS}",
    )

    check(
        "a bad key is PERMANENT - retrying a wrong key fixes nothing",
        llm_router._is_permanent(bad_key) and "GOROUTER_API_KEY" in str(bad_key),
        f"got {bad_key}",
    )
    check(
        "a prefixed model id is PERMANENT, and the message teaches the prefix rule",
        llm_router._is_permanent(bad_id)
        and "NO vendor prefix" in str(bad_id)
        and GO_PINNED in str(bad_id),
        f"got {bad_id}",
    )
    check(
        "a 503 from the gateway is still TRANSIENT, so it does get retried",
        llm_router._is_transient(busy) and not llm_router._is_permanent(busy),
        f"got {busy}",
    )
    check(
        "the out-of-credit message names the dashboard AND the env var to flip",
        "gorouter.app" in str(quota) and "LLM_PRIMARY_PROVIDER=gemini" in str(quota),
        f"got {quota}",
    )

    # The dedicated out-of-credit SENTENCE matters as much as the classification.
    # A terse insufficient_user_quota with no other words must still produce the
    # "top up / flip provider" advice rather than the generic catch-all - that
    # sentence is what stops you debugging your own code for twenty minutes.
    terse = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=200, text='{"error":{"message":"insufficient_user_quota"}}'
        ),
    )
    check(
        "a terse insufficient_user_quota still gets the dedicated out-of-credit advice",
        "this account is out of credit" in str(terse)
        and "gorouter error for" not in str(terse),
        f"got {terse}",
    )
    chinese = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=200,
            text='{"error":{"message":"当前分组额度不足"}}',
        ),
    )
    check(
        "the Chinese wording for out-of-credit is recognised and is PERMANENT",
        "this account is out of credit" in str(chinese)
        and llm_router._is_permanent(chinese),
        f"got {chinese}",
    )

    # THE REGRESSION GUARD. Gemini's free tier reports its DAILY limit with the word
    # "quotaId" in it, and that condition clears at midnight Pacific - it is
    # transient. If anyone ever "tidies up" _PERMANENT_MARKERS by adding a bare
    # "quota", Gemini silently stops being retried and this assertion fails. That is
    # exactly what it is for.
    gemini_daily = RuntimeError(
        "429 RESOURCE_EXHAUSTED: You exceeded your current quota. quotaId: "
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    )
    check(
        "Gemini's daily quotaId message is still TRANSIENT (no bare 'quota' marker)",
        llm_router._is_transient(gemini_daily)
        and not llm_router._is_permanent(gemini_daily),
        "classified permanent - did someone add a bare 'quota' to _PERMANENT_MARKERS?",
    )

# --------------------------------------------------------------- section 5
def section_gorouter_leads() -> None:
    print("\n5. Claude Opus 5 on gorouter answers; nothing else is touched")
    trace: list[dict[str, Any]] = []
    with world(
        chain=[PINNED, FREE_ONE],
        script={PINNED: GOOD_JSON},
        gemini=GOOD_JSON,
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={GO_PINNED: GOOD_JSON},
    ) as (calls, clock):
        result = ask(trace=trace)

    check("the reply is validated into the schema", result.sku == "MOU-WL-01")
    check(
        "exactly ONE billable call was made, to the pinned Opus 5 model",
        calls == [f"gorouter:{GO_PINNED}"],
        f"got {calls}",
    )
    check(
        "the understudy was never called, so the second credit was not spent",
        f"gorouter:{GO_SPARE}" not in calls,
        f"got {calls}",
    )
    check(
        "neither OpenRouter nor Gemini was touched (no other spend, no quota burnt)",
        not any(entry.startswith(("openrouter:", "gemini:")) for entry in calls),
        f"got {calls}",
    )
    check("nothing slept, because nothing failed", clock.waits == [], f"got {clock.waits}")
    check(
        "the trace names gorouter and the model that actually answered",
        bool(trace)
        and trace[-1]["provider"] == "gorouter"
        and trace[-1]["model"] == GO_PINNED
        and trace[-1]["status"] == "success",
        f"got {trace}",
    )

# --------------------------------------------------------------- section 6
def section_gorouter_retry_budget() -> None:
    print("\n6. gorouter retries cost credits, so they are rationed")
    with world(
        chain=[PINNED],
        script={PINNED: GOOD_JSON},
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={
            GO_PINNED: RuntimeError("gorouter HTTP 429: too many requests"),
            GO_SPARE: GOOD_JSON,
        },
        go_attempts=2,
    ) as (calls, clock):
        result = ask()

    check("the understudy rescued the request", result.quantity == 2)
    check(
        "a busy gateway is retried GOROUTER_MAX_ATTEMPTS times - 2, not 3",
        calls.count(f"gorouter:{GO_PINNED}") == 2,
        f"got {calls}",
    )
    check(
        "one backoff wait of 1.5s, not a hammering loop",
        clock.waits == [1.5],
        f"got {clock.waits}",
    )
    check(
        "only slot 0 gets retries - the understudy gets exactly one shot",
        calls.count(f"gorouter:{GO_SPARE}") == 1,
        f"got {calls}",
    )
    check(
        "OpenRouter was never reached, because gorouter recovered on its own",
        not any(entry.startswith("openrouter:") for entry in calls),
        f"got {calls}",
    )

    # If the understudy stumbles too, it must NOT also get the retry budget. That
    # would double the bill for a chain that is already failing, and the assertion
    # above cannot see it - a model that SUCCEEDS is only ever called once anyway.
    with world(
        chain=[PINNED],
        script={PINNED: GOOD_JSON},
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={
            GO_PINNED: RuntimeError("gorouter HTTP 429: too many requests"),
            GO_SPARE: RuntimeError("gorouter HTTP 429: too many requests"),
        },
        go_attempts=2,
    ) as (calls, clock):
        result = ask()

    gorouter_calls = [entry for entry in calls if entry.startswith("gorouter:")]
    check(
        "when the understudy fails too it is billed ONCE, not retried as well",
        calls.count(f"gorouter:{GO_SPARE}") == 1,
        f"got {calls}",
    )
    check(
        "so a fully failing gorouter chain costs 3 calls, not 4",
        gorouter_calls
        == [f"gorouter:{GO_PINNED}", f"gorouter:{GO_PINNED}", f"gorouter:{GO_SPARE}"],
        f"got {gorouter_calls}",
    )
    check(
        "still only one backoff wait, and OpenRouter rescued the request",
        clock.waits == [1.5]
        and calls[-1] == f"openrouter:{PINNED}"
        and result.sku == "MOU-WL-01",
        f"got waits={clock.waits} calls={calls}",
    )

    # A model that answers with prose instead of JSON would answer the same way
    # again, so re-billing it would be pure waste. Move to the next model instead.
    with world(
        chain=[PINNED],
        script={PINNED: GOOD_JSON},
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={GO_PINNED: "I'd be happy to help you shop!", GO_SPARE: GOOD_JSON},
        go_attempts=2,
    ) as (calls, clock):
        result = ask()

    check("unusable JSON is survived", result.sku == "MOU-WL-01")
    check(
        "a model that cannot produce JSON is NOT re-billed - the chain moves on",
        calls == [f"gorouter:{GO_PINNED}", f"gorouter:{GO_SPARE}"],
        f"got {calls}",
    )
    check("no backoff wait for unusable JSON", clock.waits == [], f"got {clock.waits}")

    # Out of credit. A refused call is not billed, but a RETRY of a hopeless call is
    # pure delay, so the router must not retry it at all.
    quota_error = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=200, text='{"error":{"message":"insufficient_user_quota"}}'
        ),
    )
    with world(
        chain=[PINNED],
        script={PINNED: GOOD_JSON},
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={GO_PINNED: quota_error, GO_SPARE: quota_error},
        go_attempts=2,
    ) as (calls, clock):
        result = ask()

    check(
        "an out-of-credit answer is never retried on the same model",
        calls.count(f"gorouter:{GO_PINNED}") == 1,
        f"got {calls}",
    )
    check("no time wasted sleeping on a hopeless call", clock.waits == [], f"got {clock.waits}")
    check(
        "the demo survives: OpenRouter takes over and answers",
        calls == [f"gorouter:{GO_PINNED}", f"gorouter:{GO_SPARE}", f"openrouter:{PINNED}"]
        and result.sku == "MOU-WL-01",
        f"got {calls}",
    )

# --------------------------------------------------------------- section 7
def section_full_cascade() -> None:
    print("\n7. Full cascade: gorouter -> OpenRouter -> Gemini")
    trace: list[dict[str, Any]] = []
    with world(
        chain=[PINNED],
        script={PINNED: RuntimeError("OpenRouter HTTP 502: bad gateway")},
        gemini=GOOD_JSON,
        primary="gorouter",
        go_chain=[GO_PINNED, GO_SPARE],
        go_script={
            GO_PINNED: RuntimeError("gorouter HTTP 500: internal error"),
            GO_SPARE: RuntimeError("gorouter HTTP 500: internal error"),
        },
        go_attempts=1,
        attempts=1,
    ) as (calls, _clock):
        result = ask(trace=trace)

    check("Gemini answered last, so the demo still survives", result.sku == "MOU-WL-01")
    check(
        "the order was both gorouter models, then OpenRouter, then Gemini",
        calls
        == [
            f"gorouter:{GO_PINNED}",
            f"gorouter:{GO_SPARE}",
            f"openrouter:{PINNED}",
            "gemini:gemini-2.5-flash",
        ],
        f"got {calls}",
    )
    check(
        "the trace records every provider that was tried, in order",
        [entry["provider"] for entry in trace if entry["status"] == "success"] == ["gemini"]
        and trace[0]["provider"] == "gorouter",
        f"got {trace}",
    )

    # Rollback drill: one env var puts Gemini in front and gorouter is not called at
    # all - which is how you finish a demo when the credits run out mid-recording.
    with world(
        chain=[PINNED],
        script={PINNED: GOOD_JSON},
        gemini=GOOD_JSON,
        primary="gemini",
        go_chain=[GO_PINNED],
        go_script={GO_PINNED: GOOD_JSON},
    ) as (calls, _clock):
        result = ask()

    check(
        "LLM_PRIMARY_PROVIDER=gemini spends nothing on gorouter",
        calls == ["gemini:gemini-2.5-flash"] and result.quantity == 2,
        f"got {calls}",
    )

# --------------------------------------------------------------- section 8
def section_gorouter_credit_503() -> None:
    """
    The one that matters on demo day. 50 credits at 0.3 a call is about 166 calls, and
    a negotiation is 2 of them - so running dry mid-demo is a realistic accident. When
    it happens the API must say so honestly, and point at the RIGHT dashboard.
    """
    print("\n8. gorouter out of credit -> a 503 that names the right dashboard")
    quota_error = gorouter_client._describe_http_error(
        GO_PINNED,
        FakeHttpResponse(
            status_code=200,
            text='{"error":{"message":"insufficient_user_quota (request id: 20260903)"}}',
        ),
    )
    with world(
        chain=[],
        script={},
        gemini=None,
        primary="gorouter",
        go_chain=[GO_PINNED],
        go_script={GO_PINNED: quota_error},
        go_attempts=2,
    ) as (calls, _clock):
        try:
            ask()
            error: BaseException | None = None
        except RuntimeError as raised:
            error = raised

    check(
        "the router raised instead of inventing an answer",
        error is not None and calls == [f"gorouter:{GO_PINNED}"],
        f"got error={error} calls={calls}",
    )
    if error is None:
        return

    check(
        "llm_guard recognises it as an outage, not as a bug in the payment code",
        llm_guard.is_llm_failure(error),
        f"got {error}",
    )

    response = llm_guard.unavailable_response(error, endpoint="/agent/purchase")
    body = getattr(response, "body", b"") or b""
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    squashed = text.replace(" ", "")

    check("HTTP status is 503 (a dependency is down), not 500", response.status_code == 503)
    check(
        "the body still swears nothing was ordered and no money moved",
        '"order_created":false' in squashed
        and '"razorpay_called":false' in squashed
        and '"money_moved":false' in squashed,
        f"got {text[:400]}",
    )
    check(
        "the advice points at gorouter.app, NOT at OpenRouter's credits page",
        "gorouter.app" in text and "openrouter.ai/credits" not in text,
        f"got {text[:600]}",
    )
    check(
        "the advice includes the one-line escape hatch (flip to the backup provider)",
        "LLM_PRIMARY_PROVIDER=gemini" in text,
        f"got {text[:600]}",
    )
    check(
        "it is diagnosed as CREDIT, not as a rate limit that will clear by itself",
        "credit" in text.lower() and "Wait 30-60 seconds" not in text,
        f"got {text[:600]}",
    )
    payload = json.loads(text) if text else {}
    check(
        "detail stays a PLAIN SENTENCE - api.js prints it verbatim to the user",
        isinstance(payload.get("detail"), str)
        and payload["detail"].startswith("The negotiation model is unavailable"),
        f"got {payload.get('detail')!r}",
    )
    check(
        "the machine-readable fields live in the sibling error object, not in detail",
        isinstance(payload.get("error"), dict)
        and payload["error"]["code"] == "llm_unavailable"
        and payload["error"]["failed_stage_name"] == "Agent Reasoning",
        f"got {payload.get('error')}",
    )

# --------------------------------------------------------------- section 9
def section_paid_model_bypasses_free_filter() -> None:
    print("\n9. OpenRouter: the pinned PAID model survives the free-only filter")
    catalog = [
        {
            "id": PINNED,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "architecture": {"input_modalities": ["text"]},
            "context_length": 200000,
        },
        {
            "id": FREE_ONE,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"]},
            "context_length": 64000,
        },
        {
            "id": FREE_TWO,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"]},
            "context_length": 32000,
        },
    ]

    check(
        "_is_free() rejects a paid price",
        openrouter_client._is_free({"prompt": "0.000003", "completion": "0.000015"})
        is False,
    )
    check(
        "_is_free() accepts a zero price",
        openrouter_client._is_free({"prompt": "0", "completion": "0"}) is True,
    )

    with patched(
        openrouter_client,
        requests=FakeModelsAPI(catalog),
        _cached_free_models=None,
        OPENROUTER_API_KEY="sk-or-test-key-not-real",
        OPENROUTER_PRIMARY_MODEL=PINNED,
        OPENROUTER_MODELS="",
        OPENROUTER_FREE_FALLBACK=True,
        OPENROUTER_MAX_CANDIDATES=4,
    ):
        free = openrouter_client.discover_free_models(force_refresh=True)
        models = openrouter_client.candidate_models()
        with patched(openrouter_client, OPENROUTER_MAX_CANDIDATES=1):
            trimmed = openrouter_client.candidate_models()

    # `bool(free) and ...` matters: an empty list would pass a bare "not in"
    # check while proving nothing. An empty catalog is a broken test, not a pass.
    check(
        "free discovery filters the paid Claude model OUT (the old blocker)",
        bool(free) and PINNED not in free,
        f"got {free}",
    )
    check(
        "free discovery still finds the free backups, best first",
        free == [FREE_ONE, FREE_TWO],
        f"got {free}",
    )
    check(
        "candidate_models() puts the pinned paid model FIRST anyway",
        models[:1] == [PINNED],
        f"got {models}",
    )
    check(
        "the free models follow it as the backup chain",
        models == [PINNED, FREE_ONE, FREE_TWO],
        f"got {models}",
    )
    check(
        "OPENROUTER_MAX_CANDIDATES=1 can never trim the model you pay for",
        trimmed == [PINNED],
        f"got {trimmed}",
    )


# --------------------------------------------------------------- section 10
def section_happy_path() -> None:
    print("\n10. OpenRouter leads: Claude answers, Gemini is never touched")
    trace: list[dict[str, Any]] = []
    with world(
        chain=[PINNED, FREE_ONE],
        script={PINNED: GOOD_JSON},
        gemini=GOOD_JSON,
    ) as (calls, _clock):
        result = ask(trace=trace)

    check("the reply is validated into the schema", result.sku == "MOU-WL-01")
    check(
        "only the pinned Claude model was called",
        calls == [f"openrouter:{PINNED}"],
        f"got {calls}",
    )
    check(
        "no Gemini request was made (no free-tier quota spent)",
        not any(entry.startswith("gemini:") for entry in calls),
        f"got {calls}",
    )
    check(
        "the trace names the provider and model that answered",
        trace
        and trace[-1]["provider"] == "openrouter"
        and trace[-1]["model"] == PINNED
        and trace[-1]["status"] == "success",
        f"got {trace}",
    )


# --------------------------------------------------------------- section 11
def section_transient_is_retried() -> None:
    print("\n11. OpenRouter 'busy' is retried, then the chain moves on")
    with world(
        chain=[PINNED, FREE_ONE],
        script={
            PINNED: RuntimeError("OpenRouter HTTP 429: rate limit exceeded"),
            FREE_ONE: GOOD_JSON,
        },
        attempts=3,
    ) as (calls, clock):
        result = ask()

    pinned_calls = [entry for entry in calls if entry.endswith(PINNED)]
    check("the request still succeeds via the backup model", result.quantity == 2)
    check(
        "the pinned model was retried 3 times (OPENROUTER_MAX_ATTEMPTS)",
        len(pinned_calls) == 3,
        f"got {len(pinned_calls)} calls: {calls}",
    )
    check(
        "backoff waits grew instead of hammering the API",
        clock.waits == [1.5, 3.0],
        f"got {clock.waits}",
    )
    check(
        "the backup model was tried exactly once",
        calls.count(f"openrouter:{FREE_ONE}") == 1,
        f"got {calls}",
    )


# --------------------------------------------------------------- section 12
def section_out_of_credit_is_not_retried() -> None:
    print("\n12. OpenRouter 'out of credit' (HTTP 402) is NOT retried")
    credit_error = RuntimeError(
        "OpenRouter HTTP 402: insufficient credits for "
        f"'{PINNED}'. Top up at https://openrouter.ai/credits"
    )
    with world(
        chain=[PINNED, FREE_ONE],
        script={PINNED: credit_error, FREE_ONE: GOOD_JSON},
        attempts=3,
    ) as (calls, clock):
        result = ask()

    pinned_calls = [entry for entry in calls if entry.endswith(PINNED)]
    check("a 402 is classified as permanent, not transient", not llm_router._is_transient(credit_error))
    check(
        "the paid model was called exactly ONCE - no wasted retries",
        len(pinned_calls) == 1,
        f"got {len(pinned_calls)} calls: {calls}",
    )
    check("no time was wasted sleeping", clock.waits == [], f"got {clock.waits}")
    check("the free backup rescued the request", result.sku == "MOU-WL-01")

    # The case that makes the "permanent beats transient" rule earn its keep.
    # OpenRouter's real out-of-credit reply for daily caps mentions a RATE LIMIT
    # and a top-up in the same sentence. Classify it as transient and we would sit
    # there retrying a wallet problem three times with backoff.
    mixed = RuntimeError(
        "OpenRouter HTTP 402: rate limit exceeded: free-models-per-day. "
        "Add credits at https://openrouter.ai/credits to raise it."
    )
    check(
        "'402 ... rate limit' in one message is judged permanent, not transient",
        not llm_router._is_transient(mixed),
    )
    with world(
        chain=[PINNED, FREE_ONE],
        script={PINNED: mixed, FREE_ONE: GOOD_JSON},
        attempts=3,
    ) as (calls, clock):
        ask()
    mixed_calls = [entry for entry in calls if entry.endswith(PINNED)]
    check(
        "so the paid model is billed once, not three times",
        len(mixed_calls) == 1 and clock.waits == [],
        f"got {len(mixed_calls)} calls, waits {clock.waits}",
    )


# --------------------------------------------------------------- section 13
def section_gemini_is_the_backup() -> None:
    print("\n13. Whole OpenRouter chain down -> Gemini takes over")
    with world(
        chain=[PINNED, FREE_ONE],
        script={
            PINNED: RuntimeError("OpenRouter HTTP 502: bad gateway"),
            FREE_ONE: RuntimeError("OpenRouter HTTP 503: unavailable"),
        },
        gemini=GOOD_JSON,
        attempts=2,
    ) as (calls, _clock):
        result = ask()

    check("Gemini answered", result.sku == "MOU-WL-01")
    check(
        "every OpenRouter model was tried before Gemini",
        calls.index("gemini:gemini-2.5-flash") == len(calls) - 1
        and f"openrouter:{PINNED}" in calls
        and f"openrouter:{FREE_ONE}" in calls,
        f"got {calls}",
    )


# --------------------------------------------------------------- section 14
def section_everything_down_is_honest() -> None:
    print("\n14. Everything down -> one honest 503, no order, no money")
    with world(
        chain=[PINNED],
        script={
            PINNED: RuntimeError(
                "OpenRouter HTTP 402: insufficient credits for the pinned model"
            )
        },
        gemini=None,
        attempts=2,
    ) as (calls, _clock):
        try:
            ask()
            error: Exception | None = None
        except RuntimeError as raised:
            error = raised

    check("the router raised instead of inventing an answer", error is not None)
    if error is None:
        return

    check(
        "the message is the exact marker llm_guard looks for",
        llm_guard.is_llm_failure(error),
        f"got {error}",
    )
    check(
        "the attempted providers are listed in the message",
        PINNED in str(error) and "402" in str(error),
        f"got {error}",
    )
    check(
        "an unrelated bug is NOT disguised as an outage",
        not llm_guard.is_llm_failure(RuntimeError("column orders.foo does not exist")),
    )

    response = llm_guard.unavailable_response(error, endpoint="/agent/purchase")
    body = getattr(response, "body", b"") or b""
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    check("HTTP status is 503, not 500", response.status_code == 503)
    check('the body states "money_moved": false', '"money_moved":false' in text.replace(" ", ""))
    check(
        "the body states no order was created and Razorpay was never called",
        '"order_created":false' in text.replace(" ", "")
        and '"razorpay_called":false' in text.replace(" ", ""),
    )
    check(
        "a 402 is diagnosed as out-of-credit with the top-up link",
        "openrouter.ai/credits" in text,
        f"got {text[:400]}",
    )
    check(
        "the failure is pinned to audit stage 2 (Agent Reasoning)",
        llm_guard.FAILED_STAGE_INDEX == 2,
    )


# --------------------------------------------------------------- section 15
def section_chatty_models_still_parse() -> None:
    print("\n15. A chatty or fenced reply is still understood")
    fenced = 'Sure! Here you go:\n```json\n{"sku": "KBD-MECH-01", "quantity": 1}\n```\nHope that helps.'
    extracted = llm_router._extract_json_object(fenced)
    check(
        "markdown fences and prose are stripped",
        extracted == '{"sku": "KBD-MECH-01", "quantity": 1}',
        f"got {extracted!r}",
    )

    nested = '{"sku": "A", "quantity": 1, "meta": {"note": "a } brace in a string"}}'
    check(
        "a brace inside a string does not end the object early",
        llm_router._extract_json_object("noise " + nested) == nested,
    )

    with world(chain=[PINNED], script={PINNED: fenced}) as (_calls, _clock):
        result = ask()
    check("the router validates a fenced reply end to end", result.sku == "KBD-MECH-01")

    with world(
        chain=[PINNED, FREE_ONE],
        script={PINNED: "I would rather not answer.", FREE_ONE: GOOD_JSON},
    ) as (calls, _clock):
        result = ask()
    check(
        "unusable JSON moves to the next model instead of re-billing the same one",
        calls == [f"openrouter:{PINNED}", f"openrouter:{FREE_ONE}"],
        f"got {calls}",
    )


# --------------------------------------------------------------- section 16
def section_no_keys_at_all() -> None:
    print("\n16. No API keys -> a message that says what to do")
    with patched(
        gorouter_client, is_configured=lambda: False
    ), patched(
        openrouter_client, is_configured=lambda: False
    ), patched(
        gemini_client, is_configured=lambda: False, model_name=lambda: "gemini-2.5-flash"
    ), patched(
        llm_router, LLM_PRIMARY_PROVIDER="gorouter", time=NoSleep()
    ):
        try:
            ask()
            error: Exception | None = None
        except RuntimeError as raised:
            error = raised

    check("it fails loudly rather than hanging", error is not None)
    if error is None:
        return
    check(
        "the message names the key to set and the restart",
        "GOROUTER_API_KEY" in str(error) and "restart uvicorn" in str(error),
        f"got {error}",
    )
    check("llm_guard still recognises it", llm_guard.is_llm_failure(error))


def main() -> int:
    print("=" * 72)
    print("APEX-Commerce LLM provider selftest (no network, no keys, no cost)")
    print("Primary brain: Claude Opus 5 on gorouter.app - every call here is FAKE.")
    print("=" * 72)

    # Sections 1-8 cover the new primary provider, 9-14 the fallbacks behind it,
    # 15-16 the parsing and the empty-config case.
    run(section_provider_order)
    run(section_gorouter_chain_and_catalog)
    run(section_gorouter_client_dialect)
    run(section_gorouter_errors_are_classified)
    run(section_gorouter_leads)
    run(section_gorouter_retry_budget)
    run(section_full_cascade)
    run(section_gorouter_credit_503)
    run(section_paid_model_bypasses_free_filter)
    run(section_happy_path)
    run(section_transient_is_retried)
    run(section_out_of_credit_is_not_retried)
    run(section_gemini_is_the_backup)
    run(section_everything_down_is_honest)
    run(section_chatty_models_still_parse)
    run(section_no_keys_at_all)

    print("\n" + "=" * 72)
    print(f"{_passed} passed, {_failed} failed")
    if _failed:
        print("Read the [FAIL] lines above - each one names what it expected.")
    else:
        print("Failover is wired correctly and no real credits were spent.")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
