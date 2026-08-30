"""Offline tests for gemini_client.py's caching and 429-backoff logic - no
network access, the SDK's Client is monkeypatched with a fake."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hindu_extract.config import load_config
from hindu_extract.gemini_client import GeminiError, _cache_key, call_gemini
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


def test_429_is_retried_with_backoff_then_succeeds(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    call_count = {"n": 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "rate limited"}})
            return _fake_response({"articles": []})

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    import google.genai as genai_module

    monkeypatch.setattr(genai_module, "Client", FakeClient)
    monkeypatch.setattr("hindu_extract.gemini_client._BASE_BACKOFF_SECONDS", 0.01)

    config = load_config()
    config = config.__class__(
        **{**config.__dict__, "paths": config.paths.__class__(**{**config.paths.__dict__, "gemini_cache_root": tmp_path})}
    )

    parsed, usage = call_gemini([_line()], 1, 9.0, config, use_cache=False)

    assert call_count["n"] == 3
    assert parsed == {"articles": []}
    assert usage["cache_hit"] is False


def test_429_gives_up_after_max_retries(monkeypatch, tmp_path):
    from google.genai import errors

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "rate limited"}})

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    import google.genai as genai_module

    monkeypatch.setattr(genai_module, "Client", FakeClient)
    monkeypatch.setattr("hindu_extract.gemini_client._BASE_BACKOFF_SECONDS", 0.001)
    monkeypatch.setattr("hindu_extract.gemini_client._MAX_429_RETRIES", 2)

    config = load_config()
    config = config.__class__(
        **{**config.__dict__, "paths": config.paths.__class__(**{**config.paths.__dict__, "gemini_cache_root": tmp_path})}
    )

    from google.genai import errors as errors_mod

    with pytest.raises(errors_mod.APIError):
        call_gemini([_line()], 1, 9.0, config, use_cache=False)
