"""Wraps the google-genai SDK call for Phase 2's boundary-finding step, with
response caching keyed on hash(line_dump + prompt_version + model_id) - a
page whose lines, prompt, and model are all unchanged never calls the API
again, so iterating on the pipeline doesn't burn the daily request quota.
"""
from __future__ import annotations

import hashlib
import json
import os
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

# 429s are the API telling us the RPM/TPM ceiling was hit anyway (the
# TokenAwareLimiter reduces how often this happens but can't guarantee it,
# since the token cost of a call is only known for certain after the
# response comes back) - retried with exponential backoff rather than
# failing the page outright.
_MAX_429_RETRIES = 5
_BASE_BACKOFF_SECONDS = 2.0


class GeminiError(RuntimeError):
    pass


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


def _generate_with_backoff(client, **kwargs) -> tuple["types.GenerateContentResponse", int]:
    from google.genai import errors

    for attempt in range(_MAX_429_RETRIES + 1):
        try:
            return client.models.generate_content(**kwargs), attempt
        except errors.APIError as e:
            if e.code != 429 or attempt == _MAX_429_RETRIES:
                raise
            time.sleep(_BASE_BACKOFF_SECONDS * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


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
            detail["retry_count"] = 0
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
            response, retry_count = _generate_with_backoff(client, **generate_kwargs)
        else:
            with limiter.reserve() as reservation:
                response, retry_count = _generate_with_backoff(client, **generate_kwargs)
                if response.usage_metadata and response.usage_metadata.total_token_count:
                    reservation.finalize(response.usage_metadata.total_token_count)
        detail["retry_count"] = retry_count

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
