"""Pluggable LLM client (OpenAI-compatible chat completions) with usage tracking."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def merge(self, other: "LLMUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens

    def to_dict(self) -> dict:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def complete(self, system: str, user: str) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError

    def complete_json(self, system: str, user: str) -> tuple[dict | list, LLMResponse]:
        resp = self.complete(system, user)
        return parse_json_loose(resp.text), resp


class OpenAICompatClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: int = 120, max_tokens: int | None = None):
        if not api_key:
            raise LLMError(
                "No LLM API key configured. Set IDE_LLM_API_KEY "
                "(or use --llm-base-url with a local OpenAI-compatible server)."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> LLMResponse:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        return self._complete_with_retry(payload)

    def _complete_with_retry(self, payload: dict) -> LLMResponse:
        import time as _time

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self._do_request(payload)
            except LLMError as e:
                last_exc = e
                msg = str(e)
                # only retry on transient: 429, 5xx, network timeout
                retryable = (
                    "HTTP 429" in msg
                    or "HTTP 5" in msg
                    or "request failed" in msg
                    or "timed out" in msg.lower()
                )
                if not retryable or attempt == 2:
                    raise
                _time.sleep(2**attempt)
        raise last_exc  # type: ignore[misc]

    def _do_request(self, payload: dict) -> LLMResponse:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"LLM request failed: {e}") from e
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected LLM response shape: {e}") from e
        usage_raw = body.get("usage") or {}
        usage = LLMUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
        )
        return LLMResponse(text=text, usage=usage)


class FakeLLMClient(LLMClient):
    """Offline client for tests and demos. Returns canned JSON per stage keyword."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.usage = LLMUsage()

    def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        for marker, payload in self.responses.items():
            if marker in system or marker in user:
                self.usage.completion_tokens += len(payload) // 4
                return LLMResponse(text=payload)
        raise LLMError(f"FakeLLMClient has no canned response for this call. System started with: {system[:80]!r}")


def parse_json_loose(text: str) -> dict | list:
    """Parse JSON from an LLM reply that may be fenced or have surrounding prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min(
            (i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1),
            default=-1,
        )
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start == -1 or end <= start:
            raise LLMError(f"Could not parse JSON from LLM output: {text[:200]!r}")
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"Invalid JSON in LLM output: {e}") from e


def estimate_cost_usd(usage: LLMUsage) -> float | None:
    """R9.5: per-run cost estimate from configurable per-1M-token prices."""
    in_price = os.environ.get("IDE_PRICE_PER_1M_INPUT_TOKENS")
    out_price = os.environ.get("IDE_PRICE_PER_1M_OUTPUT_TOKENS")
    if not in_price or not out_price:
        return None
    try:
        cost = (
            usage.prompt_tokens * float(in_price) / 1_000_000
            + usage.completion_tokens * float(out_price) / 1_000_000
        )
    except ValueError:
        return None
    return round(cost, 4)


def make_client(cfg) -> LLMClient:
    if os.environ.get("IDE_FAKE_LLM") == "1":
        raise LLMError("IDE_FAKE_LLM is only supported in tests; configure IDE_LLM_API_KEY.")
    return OpenAICompatClient(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, max_tokens=getattr(cfg, "llm_max_tokens", None))
