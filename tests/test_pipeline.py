from intent_ide.config import Config
from intent_ide.llm import FakeLLMClient
from intent_ide.pipeline import (
    EXIT_AMBIGUITY,
    EXIT_DIVERGENCE,
    EXIT_OK,
    EXIT_SYSTEM_FAILURE,
    run_pipeline,
)

EXTRACT = """```json
{"claims": [
  {"type": "postcondition", "text": "sums only positive numbers",
   "source": "stated", "confidence": 1.0},
  {"type": "edge_case", "text": "empty list behavior unstated",
   "source": "inferred", "confidence": 0.7, "ambiguous": true,
   "branches": [{"interpretation": "return 0"},
                {"interpretation": "raise ValueError"}]}
]}
```"""

SYNTH_C1 = '''```python
from under_test import sum_positives

def test_C1__sums_only_positives():
    nums = [-1, 2, 3]
    result = sum_positives(nums)
    expected = 5
    assert result == expected, f"INPUT: {nums} EXPECTED: {expected} ACTUAL: {result}"
```
'''


SYNTH_C2A = '''```python
from under_test import sum_positives

def test_C2_a__empty_returns_zero():
    assert sum_positives([]) == 0
```
'''

SYNTH_C2B = '''```python
import pytest
from under_test import sum_positives

def test_C2_b__empty_raises():
    with pytest.raises(ValueError):
        sum_positives([])
```
'''


def _synth_router(payloads):
    class Router(FakeLLMClient):
        def complete(self, system, user):
            from intent_ide.llm import LLMResponse
            self.calls.append((system, user))
            for marker, payload in payloads.items():
                if f'"id": "{marker}"' in user:
                    return LLMResponse(text=payload)
            for marker, payload in self.responses.items():
                if marker in system or marker in user:
                    return LLMResponse(text=payload)
            raise AssertionError("unrouted synthesis call: " + user[:120])
    return Router({})


def test_pipeline_divergence_end_to_end(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "under_test.py").write_text(
        "def sum_positives(nums):\n    return sum(nums)\n", encoding="utf-8")
    llm = _synth_router({"C1": SYNTH_C1, "C2.a": SYNTH_C2A, "C2.b": SYNTH_C2B})
    llm.responses["intent-extraction"] = EXTRACT

    result = run_pipeline(llm, "sum positive numbers", project,
                          tmp_path / ".ide", cfg=Config())
    assert result.exit_code == EXIT_DIVERGENCE
    assert "Coverage Summary" in result.report_markdown
    assert (tmp_path / ".ide" / "report.md").exists()
    spec_files = list((tmp_path / ".ide").glob("intentspec.v*.json"))
    assert spec_files
    assert result.usage["claims_total"] >= 2


def test_pipeline_system_failure_is_distinct(tmp_path):
    class BrokenLLM(FakeLLMClient):
        def complete(self, system, user):
            raise RuntimeError("provider down")

    result = run_pipeline(BrokenLLM({}), "task text",
                          tmp_path / "proj", tmp_path / ".ide", cfg=Config())
    assert result.exit_code == EXIT_SYSTEM_FAILURE
    assert "NOT a finding about your code" in result.report_markdown


def test_exit_codes_constants():
    assert (EXIT_OK, EXIT_DIVERGENCE, EXIT_AMBIGUITY, EXIT_SYSTEM_FAILURE) == (0, 1, 2, 3)
