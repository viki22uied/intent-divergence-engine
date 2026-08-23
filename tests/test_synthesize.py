import ast

from intent_ide.llm import FakeLLMClient
from intent_ide.models import IntentSpec
from intent_ide.synthesize import synthesize_suite


def _payload(func_name: str, helper: str = "") -> str:
    return f'''```python
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

{helper}

def {func_name}():
    assert True
```
'''


def make_llm() -> FakeLLMClient:
    return FakeLLMClient({
        '"id": "C1"': _payload("test_C1__sums_positives", "def helper():\n    return 1"),
        '"id": "C2.a"': _payload("test_C2_a__empty_returns_zero"),
        '"id": "C2.b"': _payload("test_C2_b__empty_raises"),
    })


def make_spec() -> IntentSpec:
    return IntentSpec.build("task", [
        {"type": "postcondition", "text": "sums positive numbers only",
         "source": "stated", "confidence": 1.0},
        {"type": "edge_case", "text": "behavior on empty list unstated",
         "source": "inferred", "confidence": 0.7, "ambiguous": True,
         "branches": [{"interpretation": "return 0"},
                      {"interpretation": "raise ValueError"}]},
    ])


def test_one_file_per_branch(tmp_path):
    suite = synthesize_suite(make_llm(), make_spec(), "", tmp_path, 3, 50)
    names = [p.name for p in suite.files]
    assert len(suite.files) == 3
    assert any("C2_a" in n for n in names)
    assert any("C2_b" in n for n in names)


def test_traceability_map_complete(tmp_path):
    suite = synthesize_suite(make_llm(), make_spec(), "", tmp_path, 3, 50)
    fnames = {t.function_name for t in suite.tests}
    assert fnames == {"test_C1__sums_positives", "test_C2_a__empty_returns_zero",
                      "test_C2_b__empty_raises"}
    assert all(t.claim_id for t in suite.tests)


def test_generated_files_are_valid_python(tmp_path):
    suite = synthesize_suite(make_llm(), make_spec(), "", tmp_path, 3, 50)
    for p in suite.files:
        ast.parse(p.read_text(encoding="utf-8"))


def test_cap_enforced(tmp_path):
    payload = '''```python
import pytest

def test_C9__a():
    assert True

def test_C9__b():
    assert True

def test_not_traceable():
    assert True
```
'''

    class OneShot(FakeLLMClient):
        def complete(self, system, user):
            from intent_ide.llm import LLMResponse
            self.calls.append((system, user))
            return LLMResponse(text=payload)

    spec = IntentSpec.build("t", [{"type": "postcondition", "text": "x does y",
                                   "source": "stated", "confidence": 1.0}])
    spec.claims[0].id = "C9"
    suite = synthesize_suite(OneShot({}), spec, "", tmp_path,
                             max_tests_per_claim=2, max_examples=10)
    code = suite.files[0].read_text(encoding="utf-8")
    assert "test_C9__a" in code and "test_C9__b" in code
    assert "not_traceable" not in code
