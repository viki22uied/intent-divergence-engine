"""Runtime configuration (R9.5 cost caps, R3.2 timeouts)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.isdigit() else default


@dataclass
class Config:
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    max_tests_per_claim: int = 3
    suite_timeout_seconds: int = 300
    test_timeout_seconds: int = 30
    max_hypothesis_examples: int = 100
    max_claims: int = 25
    max_task_chars: int = 8000
    llm_max_tokens: int = 2000
    memory_limit_mb: int = 1024

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            llm_base_url=os.environ.get("IDE_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.environ.get("IDE_LLM_API_KEY", ""),
            llm_model=os.environ.get("IDE_LLM_MODEL", cls.llm_model),
            max_tests_per_claim=_int_env("IDE_MAX_TESTS_PER_CLAIM", 3),
            suite_timeout_seconds=_int_env("IDE_SUITE_TIMEOUT_SECONDS", 300),
            test_timeout_seconds=_int_env("IDE_TEST_TIMEOUT_SECONDS", 30),
            max_hypothesis_examples=_int_env("IDE_MAX_HYPOTHESIS_EXAMPLES", 100),
            max_claims=_int_env("IDE_MAX_CLAIMS", 25),
            max_task_chars=_int_env("IDE_MAX_TASK_CHARS", 8000),
            llm_max_tokens=_int_env("IDE_LLM_MAX_TOKENS", 2000),
            memory_limit_mb=_int_env("IDE_MEMORY_LIMIT_MB", 1024),
        )
