"""Offline end-to-end demo: no API key needed (uses a scripted fake LLM).

Run from repo root:
    python examples/demo.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from intent_ide.config import Config
from intent_ide.llm import FakeLLMClient
from intent_ide.pipeline import run_pipeline

EXTRACT = """{"claims": [
  {"type": "postcondition", "text": "Cart total sums only positive-priced items",
   "source": "stated", "confidence": 1.0},
  {"type": "edge_case", "text": "Behavior for an empty cart is unstated",
   "source": "inferred", "confidence": 0.7, "ambiguous": true,
   "branches": [{"interpretation": "return 0"},
                {"interpretation": "raise ValueError"}]},
  {"type": "postcondition", "text": "Total is rounded to two decimal places",
   "source": "stated", "confidence": 0.9}
]}"""

SYNTH = {
    "C1": '''```python
from under_test import cart_total

def test_C1__sums_only_positive_items():
    items = [{"price": -5.0}, {"price": 3.0}]
    result = cart_total(items)
    expected = 3.0
    assert result == expected, f"INPUT: {items} EXPECTED: {expected} ACTUAL: {result}"
```''',
    "C2.a": '''```python
from under_test import cart_total

def test_C2_a__empty_cart_returns_zero():
    result = cart_total([])
    expected = 0
    assert result == expected, f"INPUT: [] EXPECTED: {expected} ACTUAL: {result}"
```''',
    "C2.b": '''```python
import pytest
from under_test import cart_total

def test_C2_b__empty_cart_raises():
    with pytest.raises(ValueError):
        cart_total([])
```''',
    "C3": '''```python
from under_test import cart_total

def test_C3__rounds_to_two_decimals():
    items = [{"price": 0.1}, {"price": 0.2}]
    result = cart_total(items)
    expected = 0.3
    assert result == expected, f"INPUT: {items} EXPECTED: {expected} ACTUAL: {result}"
```''',
}


class DemoLLM(FakeLLMClient):
    def complete(self, system, user):
        from intent_ide.llm import LLMResponse
        self.calls.append((system, user))
        if "intent-extraction" in system:
            return LLMResponse(text=EXTRACT)
        for marker, payload in SYNTH.items():
            if f'"id": "{marker}"' in user:
                return LLMResponse(text=payload)
        raise AssertionError("unrouted call")


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ide_demo_"))
    project = tmp / "shop"
    project.mkdir()
    # The AI-generated code: "almost right" — sums everything, negatives included.
    (project / "under_test.py").write_text(
        "def cart_total(items):\n"
        "    return sum(item['price'] for item in items)\n",
        encoding="utf-8",
    )
    llm = DemoLLM({})
    result = run_pipeline(
        llm,
        task_description=(
            "Implement cart_total(items): sum the prices of items in the cart. "
            "Negative-priced items (refunds) must not reduce the total. "
            "Round to two decimals."
        ),
        project_dir=project,
        out_dir=tmp / ".ide",
        cfg=Config(),
    )
    print(result.report_markdown)
    print(f"\n[artifacts: {tmp / '.ide'}]", file=sys.stderr)
    print(f"[exit {result.exit_code}]", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
