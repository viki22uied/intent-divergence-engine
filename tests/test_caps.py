import json

from intent_ide.extract import extract_intent
from intent_ide.llm import FakeLLMClient


def test_max_claims_cap():
    claims = [{"type": "postcondition", "text": f"claim {i}", "source": "stated", "confidence": 1.0} for i in range(100)]
    payload = json.dumps({"claims": claims})
    llm = FakeLLMClient({"intent-extraction": payload})
    spec, meta = extract_intent(llm, "task", max_claims=25, max_task_chars=8000)
    assert len(spec.claims) == 25
    assert meta["claims_dropped_by_cap"] == 75


def test_task_truncation():
    llm = FakeLLMClient({"intent-extraction": json.dumps({"claims": [{"type": "postcondition", "text": "x", "source": "stated", "confidence": 1.0}]})})
    long_task = "a" * 9000
    spec, meta = extract_intent(llm, long_task, max_claims=25, max_task_chars=100)
    assert meta["truncated_task"] is True
    assert meta["original_task_chars"] == 9000
    # task_description_hash is computed from truncated text, but we just verify it ran
    assert spec.task_description_hash
