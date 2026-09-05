"""Offline tests for gemini_client.py's caching and retry logic - no
network access, the SDK's Client is monkeypatched with a fake."""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from hindu_extract.config import load_config
from hindu_extract.gemini_client import GeminiError, QuotaExhaustedError, _cache_key, call_gemini, is_retryable
from hindu_extract.models import FontProfile, Line, LineFlags


def _line(line_no=1, text="hello world"):
    return Line(
        line_no=line_no,
        page_num=1,
        text=text,
        bbox=(0.0, 0.0, 10.0, 10.0),
        font_profile=FontProfile(name="Test", size=9.0, is_bold=False, is_italic=False, mixed=False),
        stream_start=0,
        stream_end=len(text),
        flags=LineFlags(single_glyph=False, size_outlier=False, ends_with_hyphen=False),
    )


def test_cache_key_changes_with_thinking_level():
    a = _cache_key("prompt", "v4", "model-x", "HIGH", 49152)
    b = _cache_key("prompt", "v4", "model-x", "MEDIUM", 49152)
    assert a != b


def test_cache_key_changes_with_max_output_tokens():
    a = _cache_key("prompt", "v4", "model-x", "MEDIUM", 32768)
    b = _cache_key("prompt", "v4", "model-x", "MEDIUM", 49152)
    assert a != b


def test_cache_key_stable_for_identical_inputs():
    a = _cache_key("prompt", "v4", "model-x", "MEDIUM", 49152)
    b = _cache_key("prompt", "v4", "model-x", "MEDIUM", 49152)
    assert a == b


def _fake_response(text_payload: dict):
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=2, total_token_count=17
        ),
        candidates=[SimpleNamespace(finish_reason="STOP")],
        text=json.dumps(text_payload),
    )


def _fast_retry_config(tmp_path):
    """Real config, with gemini_retry's delays shrunk to keep the test
    suite fast, and gemini_cache_root pointed at a throwaway dir so these
    tests never touch (or depend on) the real Gemini cache on disk."""
    config = load_config()
    config = dataclasses.replace(
        config,
        paths=dataclasses.replace(config.paths, gemini_cache_root=tmp_path),
        gemini_retry=dataclasses.replace(
            config.gemini_retry,
            base_delay_s=0.001,
            max_delay_s=0.01,
            quota_retry_after_cap_s=0.01,
            quota_fallback_delay_s=0.001,
        ),
    )
    return config


class _FakeClient:
    def __init__(self, models, api_key=None):
        self.models = models


def _patch_client(monkeypatch, models) -> None:
    import google.genai as genai_module

    monkeypatch.setattr(genai_module, "Client", lambda api_key=None: _FakeClient(models))


def test_503_server_error_is_retried_then_succeeds(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
            return _fake_response({"articles": []})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    parsed, usage = call_gemini([_line()], 1, 9.0, config, use_cache=False)

    assert call_count["n"] == 3
    assert parsed == {"articles": []}
    assert usage["cache_hit"] is False


def test_max_attempts_exhausted_raises_last_error(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)
    config = dataclasses.replace(config, gemini_retry=dataclasses.replace(config.gemini_retry, max_attempts=3))

    with pytest.raises(errors.ServerError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_fail_immediately_without_retry(monkeypatch, tmp_path, code):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            raise errors.ClientError(code, {"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    with pytest.raises(errors.ClientError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)
    assert call_count["n"] == 1  # no retry burned on a deterministic failure


def test_429_without_daily_signal_is_retried_once_then_succeeds(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise errors.ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "rate limited"}})
            return _fake_response({"articles": []})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    parsed, usage = call_gemini([_line()], 1, 9.0, config, use_cache=False)
    assert call_count["n"] == 2
    assert parsed == {"articles": []}


def test_429_daily_quota_fails_immediately_without_retry(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            raise errors.ClientError(
                429,
                {
                    "error": {
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "Quota exceeded for quota metric 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'",
                    }
                },
            )

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    with pytest.raises(QuotaExhaustedError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)
    assert call_count["n"] == 1  # RPD is not worth retrying against - see config/default.yaml


def test_a_failed_call_is_never_cached(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    with pytest.raises(errors.ClientError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)

    assert list(tmp_path.iterdir()) == []


def test_a_successful_retry_is_cached_exactly_like_a_first_attempt_success(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
            return _fake_response({"articles": []})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    parsed, usage = call_gemini([_line()], 1, 9.0, config, use_cache=True)
    assert usage["cache_hit"] is False

    cached_files = list(tmp_path.iterdir())
    assert len(cached_files) == 1
    cached = json.loads(cached_files[0].read_text(encoding="utf-8"))
    assert cached["response"] == {"articles": []}
    # cache content/shape is identical to a same-input first-attempt success -
    # the number of attempts it took is not part of the cache key or payload.
    assert "usage" in cached and cached["usage"]["cache_hit"] is False

    # A second call against the same (now-cached) prompt must be a cache
    # hit and must not call the SDK again.
    call_count["n"] = 0
    parsed2, usage2 = call_gemini([_line()], 1, 9.0, config, use_cache=True)
    assert call_count["n"] == 0
    assert usage2["cache_hit"] is True
    assert parsed2 == {"articles": []}


def test_retry_attempts_are_recorded_on_the_gemini_call_trace_event(monkeypatch, tmp_path):
    from google.genai import errors
    from hindu_extract.trace import RunTracer, new_run_id

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
            return _fake_response({"articles": []})

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    db_path = tmp_path / "trace.db"
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)

    call_gemini([_line()], 1, 9.0, config, use_cache=False, tracer=tracer)

    from hindu_extract.trace import get_page_stages

    events = get_page_stages(db_path, tracer.run_id, 1)
    gemini_events = [e for e in events if e["stage"] == "gemini_call"]
    assert len(gemini_events) == 1
    detail = gemini_events[0]["detail"]
    assert detail["attempt_count"] == 3
    assert len(detail["attempts"]) == 3
    assert detail["attempts"][0]["error_code"] == 503
    assert detail["attempts"][-1]["error_code"] is None
    assert detail["sleep_total_s"] > 0


def test_gemini_error_from_max_tokens_truncation_is_not_retried(monkeypatch, tmp_path):
    """MAX_TOKENS truncation is detected after a successful call returns -
    it's deterministic given the same input, so retrying would just burn
    quota on an identical failure (see gemini_client.call_gemini)."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    call_count = {"n": 0}

    def _truncated_response():
        return SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=10, candidates_token_count=49000, thoughts_token_count=100, total_token_count=49110
            ),
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
            text="",
        )

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            return _truncated_response()

    _patch_client(monkeypatch, FakeModels())
    config = _fast_retry_config(tmp_path)

    with pytest.raises(GeminiError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)
    assert call_count["n"] == 1


# --- is_retryable: the single flag reused everywhere a page's failure
# needs to know "would trying this exact call again have a chance of
# succeeding" - retry ladder classification, page_errors persistence, and
# the Page Reader's "Retry this page" button all defer to this one
# function rather than each maintaining their own judgment.


@pytest.mark.parametrize("code", [503, 500, 504])
def test_server_errors_are_retryable(code):
    from google.genai import errors

    error = errors.ServerError(code, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
    assert is_retryable(error) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_non_429_client_errors_are_not_retryable(code):
    from google.genai import errors

    error = errors.ClientError(code, {"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}})
    assert is_retryable(error) is False


def test_429_without_daily_signal_is_retryable():
    from google.genai import errors

    error = errors.ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "rate limited"}})
    assert is_retryable(error) is True


def test_max_tokens_gemini_error_is_not_retryable():
    assert is_retryable(GeminiError("response truncated at max_output_tokens")) is False


def test_quota_exhausted_error_is_not_retryable():
    assert is_retryable(QuotaExhaustedError("daily quota exhausted")) is False


def test_a_canary_or_phase1_hard_fail_lands_on_the_non_retryable_side():
    """pipeline.EmptyPageError (a Phase 1 hard-fail - e.g. a corrupt or
    image-only page) isn't a Gemini/transport error at all, so it falls
    through _classify_error's default - confirmed here to land as
    non-retryable, same as the Page Reader needs it to: re-running Phase 1
    against the same PDF bytes reproduces the same zero-line result, not a
    fresh chance."""
    from hindu_extract.pipeline import EmptyPageError

    assert is_retryable(EmptyPageError("page 1 produced zero lines")) is False
