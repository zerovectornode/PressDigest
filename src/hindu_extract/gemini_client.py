"""Wraps the google-genai SDK call for Phase 2's boundary-finding step, with
response caching keyed on hash(line_dump + prompt_version + model_id) - a
page whose lines, prompt, and model are all unchanged never calls the API
again, so iterating on the pipeline doesn't burn the daily request quota.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from contextlib import nullcontext
from pathlib import Path

from dotenv import load_dotenv

from hindu_extract.config import Config
from hindu_extract.gemini_prompt import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from hindu_extract.models import Line
from hindu_extract.rate_limit import TokenAwareLimiter
from hindu_extract.trace import RunTracer

_dotenv_loaded = False
_logger = logging.getLogger("hindu_extract.gemini_client")

_RETRY_INFO_TYPE_MARKER = "RetryInfo"
_RETRY_DELAY_RE = re.compile(r"([\d.]+)s?$")


class GeminiError(RuntimeError):
    pass


class QuotaExhaustedError(GeminiError):
    """Raised when a 429 looks like the daily (RPD) quota, not a transient
    RPM/TPM ceiling - retrying is not useful since RPD only resets at
    midnight Pacific (~12:30 PM IST), which no single pipeline run can wait
    for. See config/default.yaml "gemini_retry" for why RPM/TPM exhaustion
    is implausible at this pipeline's call volume, so a 429 that isn't
    obviously RPD-tagged is still treated as a one-shot-retryable ceiling
    rather than assumed to be RPD."""


def _ensure_dotenv_loaded() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True


def _get_api_key() -> str:
    _ensure_dotenv_loaded()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError(
            "GEMINI_API_KEY is not set. Create a .env file in the project root "
            "with GEMINI_API_KEY=<your key> (see .env.example)."
        )
    return api_key


def _cache_key(user_prompt: str, prompt_version: str, model: str, thinking_level: str, max_output_tokens: int) -> str:
    # thinking_level and max_output_tokens are generation settings, not just
    # bookkeeping - a different thinking_level can legitimately produce a
    # different response for the same prompt (see the Step A latency
    # experiment: HIGH/MEDIUM/LOW all called with identical prompts). They
    # must be part of the key or switching thinking_level would silently
    # serve a response generated under the old setting.
    h = hashlib.sha256()
    h.update(user_prompt.encode("utf-8"))
    h.update(b"\0")
    h.update(prompt_version.encode("utf-8"))
    h.update(b"\0")
    h.update(model.encode("utf-8"))
    h.update(b"\0")
    h.update(thinking_level.encode("utf-8"))
    h.update(b"\0")
    h.update(str(max_output_tokens).encode("utf-8"))
    return h.hexdigest()[:24]


def _cache_path(config: Config, key: str) -> Path:
    return config.gemini_cache_root / f"{key}.json"


def _classify_error(e: Exception) -> tuple[bool, bool, int | str | None, str]:
    """Returns (retryable, is_429, code, message). 503/500/504 (ServerError)
    and connection/timeout errors (raised by the SDK's underlying
    httpx/requests transport, not wrapped in APIError - there's no response
    to build one from) are retryable; 429 is its own separate path (see
    _generate_with_retry); any other ClientError (400/401/403/404) and
    everything else is not retryable - burning an identical, deterministic
    failure again wastes quota for nothing."""
    from google.genai import errors

    if isinstance(e, errors.APIError):
        code = e.code
        message = e.message or str(e)
        if code == 429:
            return True, True, code, message
        return isinstance(e, errors.ServerError), False, code, message

    import httpx
    import requests

    if isinstance(
        e,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.NetworkError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    ):
        return True, False, None, str(e)
    return False, False, None, str(e)


def is_retryable(error: Exception) -> bool:
    """The single source of truth for "would trying this exact call again
    have a chance of succeeding" - used both by the retry ladder itself
    (via _classify_error) and, once a page has already failed for good,
    to decide whether "Retry this page" should even be offered (see
    page_errors.PageError.retryable / jobs.py). Deliberately reuses
    _classify_error's flag rather than a second, separate severity
    taxonomy - a page-level "retryable" that disagreed with the ladder's
    own judgment about the same exception would be a bug waiting to
    happen. GeminiError (MAX_TOKENS truncation, empty response,
    QuotaExhaustedError's RPD case) is always non-retryable: all three are
    deterministic given the same input, and QuotaExhaustedError already
    means "don't retry, see message" by construction. Anything that isn't
    a GeminiError or a recognized transport/API error (e.g.
    pipeline.EmptyPageError, a Phase 1 hard-fail from a corrupt or
    image-only page) falls through to _classify_error's default of
    non-retryable too - re-running Phase 1 against the same PDF bytes
    produces the same result, not a fresh chance."""
    if isinstance(error, GeminiError):
        return False
    retryable, _is_429, _code, _message = _classify_error(error)
    return retryable


def _is_daily_quota_exhausted(e: Exception) -> bool:
    """Best-effort: the SDK doesn't give a structured quota-metric field to
    check exactly, so this looks for "day" alongside quota language in the
    message/details - real RPD-exhausted responses name the metric (e.g.
    "...PerDay...") or say "daily" in the message. Deliberately
    conservative: an ambiguous 429 is treated as the retryable RPM/TPM path
    below, not assumed to be RPD, since at 21 calls/edition against 15 RPM
    that path firing at all is already the surprising case worth logging
    rather than silently reclassifying."""
    text = f"{getattr(e, 'message', '') or ''} {getattr(e, 'details', '') or ''}".lower()
    return "day" in text and ("quota" in text or "exceeded" in text or "resource_exhausted" in text)


def _extract_retry_delay_s(e: Exception) -> float | None:
    """Honours the API's own hint for how long to wait: an HTTP Retry-After
    header on the raw response if one is attached to the exception, else a
    google.rpc.RetryInfo structured detail (retryDelay: "41s") in the error
    body. Returns None if neither is present, so the caller falls back to
    quota_fallback_delay_s."""
    response = getattr(e, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is not None:
        header = headers.get("retry-after") or headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass

    details = getattr(e, "details", None) or {}
    if isinstance(details, dict):
        error_details = (details.get("error") or {}).get("details") or details.get("details") or []
        for d in error_details if isinstance(error_details, list) else []:
            if isinstance(d, dict) and _RETRY_INFO_TYPE_MARKER in str(d.get("@type", "")):
                delay = d.get("retryDelay")
                if isinstance(delay, str):
                    m = _RETRY_DELAY_RE.match(delay.strip())
                    if m:
                        return float(m.group(1))
    return None


def _sleep_with_jitter(nominal_s: float, jitter: float) -> float:
    return random.uniform(nominal_s * (1 - jitter), nominal_s * (1 + jitter))


def _finalize_retry_detail(detail: dict, attempts: list[dict], sleep_total_s: float, attempt: int) -> None:
    detail["attempts"] = attempts
    detail["attempt_count"] = attempt
    detail["sleep_total_s"] = round(sleep_total_s, 3)


def _generate_with_retry(client, pipeline_config: Config, page_num: int, detail: dict, **kwargs):
    """Wraps client.models.generate_content with the retry ladder from
    config/default.yaml's gemini_retry block - the transport layer, so both
    Phase 2's per-page calls (call_gemini below) and Phase 4's ranking call
    (ranking.py, which already imports this function) are covered. Records
    per-attempt detail into `detail` (the same dict the caller's
    tracer.stage() context is already populating) rather than a new trace
    table - see design/DESIGN.md. The gemini_call stage's own duration
    naturally includes every sleep here, since it's all still inside that
    stage's `with` block.

    429 RESOURCE_EXHAUSTED is a separate policy from the 503/500/504/
    timeout ladder (see config/default.yaml's rationale): retried at most
    once, honouring retry-after/RetryInfo when present, and never retried
    at all if it looks like the daily quota - see QuotaExhaustedError.

    Named `pipeline_config` rather than `config` (this repo's usual
    parameter name for a hindu_extract.config.Config) specifically because
    **kwargs here always includes a `config=types.GenerateContentConfig(...)`
    entry for the SDK call - a same-named parameter would shadow it instead
    of forwarding it, a real bug caught while writing this."""
    from google.genai import types

    retry_cfg = pipeline_config.gemini_retry
    kwargs["config"].http_options = types.HttpOptions(timeout=int(retry_cfg.timeout_s * 1000))

    attempts: list[dict] = []
    sleep_total_s = 0.0
    quota_retried = False
    attempt = 0

    while True:
        attempt += 1
        start = time.time()
        try:
            response = client.models.generate_content(**kwargs)
        except Exception as e:  # noqa: BLE001 - classified immediately below
            elapsed_s = time.time() - start
            retryable, is_429, code, message = _classify_error(e)
            record = {"attempt": attempt, "elapsed_s": round(elapsed_s, 3), "error_code": code, "error_message": message}

            if is_429 and _is_daily_quota_exhausted(e):
                attempts.append(record)
                _finalize_retry_detail(detail, attempts, sleep_total_s, attempt)
                _logger.warning(
                    "Gemini page %s attempt %s: daily quota (RPD) appears exhausted (%s) - not retrying",
                    page_num, attempt, message,
                )
                raise QuotaExhaustedError(
                    f"page {page_num}: Gemini daily quota (RPD) appears exhausted - {message}. "
                    f"RPD resets at midnight Pacific (~12:30 PM IST); waiting is not something "
                    f"this pipeline run can do."
                ) from e

            if is_429:
                if quota_retried or attempt >= retry_cfg.max_attempts:
                    attempts.append(record)
                    _finalize_retry_detail(detail, attempts, sleep_total_s, attempt)
                    raise
                delay = _extract_retry_delay_s(e)
                sleep_s = min(delay, retry_cfg.quota_retry_after_cap_s) if delay is not None else retry_cfg.quota_fallback_delay_s
                quota_retried = True
            elif retryable and attempt < retry_cfg.max_attempts:
                nominal_s = min(retry_cfg.base_delay_s * retry_cfg.multiplier ** (attempt - 1), retry_cfg.max_delay_s)
                sleep_s = _sleep_with_jitter(nominal_s, retry_cfg.jitter)
            else:
                attempts.append(record)
                _finalize_retry_detail(detail, attempts, sleep_total_s, attempt)
                raise

            record["sleep_s"] = round(sleep_s, 3)
            attempts.append(record)
            _logger.warning(
                "Gemini retry: page %s attempt %s/%s error=%s (%s) - sleeping %.1fs",
                page_num, attempt, retry_cfg.max_attempts, code, message, sleep_s,
            )
            time.sleep(sleep_s)
            sleep_total_s += sleep_s
            continue

        elapsed_s = time.time() - start
        attempts.append({"attempt": attempt, "elapsed_s": round(elapsed_s, 3), "error_code": None, "error_message": None})
        _finalize_retry_detail(detail, attempts, sleep_total_s, attempt)
        return response


def call_gemini(
    lines: list[Line],
    page_num: int,
    modal_font_size: float,
    config: Config,
    use_cache: bool = True,
    extra_instruction: str | None = None,
    limiter: TokenAwareLimiter | None = None,
    tracer: RunTracer | None = None,
) -> tuple[dict, dict]:
    """Returns (parsed_response_json, usage_metadata_dict). If `limiter` is
    given, the network call (not a cache hit) is gated by it - see
    rate_limit.py - so callers running many pages concurrently (e.g.
    articles_pipeline.process_edition_articles) stay under the real TPM/RPM
    ceilings instead of a fixed worker-count guess. If `tracer` is given,
    emits one gemini_call stage event (model, thinking_level, token counts,
    latency, cache hit/miss, retry count, finish_reason) and persists the
    exact prompt + raw response via record_gemini_raw - see trace.py."""
    stage_ctx = tracer.stage(page_num, "gemini_call") if tracer is not None else nullcontext({})
    with stage_ctx as detail:
        detail["model"] = config.gemini.model
        detail["thinking_level"] = config.gemini.thinking_level

        user_prompt = build_user_prompt(lines, page_num, modal_font_size)
        if extra_instruction:
            user_prompt = f"{user_prompt}\n\n{extra_instruction}"

        key = _cache_key(
            user_prompt,
            config.gemini.prompt_version,
            config.gemini.model,
            config.gemini.thinking_level,
            config.gemini.max_output_tokens,
        )
        cache_path = _cache_path(config, key)

        if use_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            usage = dict(cached["usage"])
            usage["cache_hit"] = True
            detail.update(usage)
            detail["attempt_count"] = 0
            detail["finish_reason"] = "CACHED"
            if tracer is not None:
                tracer.record_gemini_raw(page_num, user_prompt, json.dumps(cached["response"], ensure_ascii=False))
            return cached["response"], usage

        # Imported lazily so importing this module (e.g. for unit tests that
        # never call the API) never requires the SDK's own heavier imports to
        # succeed, and so a missing API key is only ever raised once we're
        # actually about to make a network call.
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_get_api_key())
        generate_kwargs = dict(
            model=config.gemini.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=config.gemini.temperature,
                max_output_tokens=config.gemini.max_output_tokens,
                response_mime_type="application/json",
                response_json_schema=RESPONSE_JSON_SCHEMA,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel[config.gemini.thinking_level]
                ),
            ),
        )

        if limiter is None:
            response = _generate_with_retry(client, config, page_num, detail, **generate_kwargs)
        else:
            with limiter.reserve() as reservation:
                response = _generate_with_retry(client, config, page_num, detail, **generate_kwargs)
                if response.usage_metadata and response.usage_metadata.total_token_count:
                    reservation.finalize(response.usage_metadata.total_token_count)

        usage = {"cache_hit": False}
        if response.usage_metadata:
            um = response.usage_metadata
            usage.update(
                {
                    "prompt_token_count": um.prompt_token_count,
                    "candidates_token_count": um.candidates_token_count,
                    "thoughts_token_count": um.thoughts_token_count,
                    "total_token_count": um.total_token_count,
                }
            )
        detail.update(usage)

        finish_reason = response.candidates[0].finish_reason if response.candidates else None
        detail["finish_reason"] = str(finish_reason) if finish_reason is not None else None

        if finish_reason is not None and str(finish_reason).endswith("MAX_TOKENS"):
            raise GeminiError(
                f"page {page_num}: response truncated at max_output_tokens="
                f"{config.gemini.max_output_tokens} (thoughts="
                f"{usage.get('thoughts_token_count')}, candidates="
                f"{usage.get('candidates_token_count')}) - failing loudly instead of "
                f"attempting to parse invalid partial JSON. Lower thinking_level or "
                f"raise max_output_tokens (see config/default.yaml)."
            )

        if not response.text:
            raise GeminiError(f"empty response from Gemini for page {page_num} (finish_reason={finish_reason})")

        parsed = json.loads(response.text)

        if tracer is not None:
            tracer.record_gemini_raw(page_num, user_prompt, response.text)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"response": parsed, "usage": usage}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return parsed, usage
