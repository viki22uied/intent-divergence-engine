import json
import urllib.error

import pytest

from intent_ide.llm import LLMError, OpenAICompatClient


def _make_client():
    return OpenAICompatClient("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", timeout_seconds=5)


def test_retry_on_429_then_success(monkeypatch):
    client = _make_client()
    calls = {"n": 0}

    def fake_do_request(payload):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise LLMError(f"LLM HTTP 429: rate limited {calls['n']}")
        # success on 3rd
        from intent_ide.llm import LLMResponse, LLMUsage
        return LLMResponse(text='{"ok": true}', usage=LLMUsage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(client, "_do_request", fake_do_request)
    monkeypatch.setattr("time.sleep", lambda s: None)
    resp = client.complete("sys", "user")
    assert resp.text == '{"ok": true}'
    assert calls["n"] == 3


def test_retry_on_5xx_then_success(monkeypatch):
    client = _make_client()
    calls = {"n": 0}

    def fake_do_request(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("LLM HTTP 500: server error")
        from intent_ide.llm import LLMResponse, LLMUsage
        return LLMResponse(text='hi', usage=LLMUsage())

    monkeypatch.setattr(client, "_do_request", fake_do_request)
    monkeypatch.setattr("time.sleep", lambda s: None)
    resp = client.complete("sys", "user")
    assert calls["n"] == 2
    assert resp.text == "hi"


def test_no_retry_on_401(monkeypatch):
    client = _make_client()
    calls = {"n": 0}

    def fake_do_request(payload):
        calls["n"] += 1
        raise LLMError("LLM HTTP 401: unauthorized")

    monkeypatch.setattr(client, "_do_request", fake_do_request)
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("should not sleep on 401"))
    with pytest.raises(LLMError, match="401"):
        client.complete("sys", "user")
    assert calls["n"] == 1


def test_max_tokens_in_payload(monkeypatch):
    client = OpenAICompatClient("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", max_tokens=123)
    captured = {}

    def fake_do_request(payload):
        captured.update(payload)
        from intent_ide.llm import LLMResponse, LLMUsage
        return LLMResponse(text="ok", usage=LLMUsage())

    monkeypatch.setattr(client, "_do_request", fake_do_request)
    client.complete("sys", "user")
    assert captured["max_tokens"] == 123
