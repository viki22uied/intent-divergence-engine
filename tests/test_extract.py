from pathlib import Path

from intent_ide.extract import extract_intent
from intent_ide.llm import FakeLLMClient, parse_json_loose


PAYLOAD = """
Here is your JSON:
```json
{"claims": [
  {"type": "postcondition", "text": "sums positive numbers only",
   "source": "stated", "confidence": 1.0},
  {"type": "edge_case", "text": "behavior on empty list unstated",
   "source": "inferred", "confidence": 0.7, "ambiguous": true,
   "branches": [{"interpretation": "return 0"},
                {"interpretation": "raise ValueError"}]},
  {"type": "style", "text": "use camelCase", "source": "stated"}
]}
```
"""


def test_extract_assigns_ids_and_drops_non_behavioral():
    llm = FakeLLMClient({"intent-extraction": PAYLOAD})
    spec, meta = extract_intent(llm, "sum positive numbers")
    assert [c.id for c in spec.claims] == ["C1", "C2"]
    assert all(c.type.value != "style" for c in spec.claims)
    assert meta["claim_count"] == 2
    assert meta["checklist_run"]


def test_extract_keeps_branches_separate():
    llm = FakeLLMClient({"intent-extraction": PAYLOAD})
    spec, _ = extract_intent(llm, "task")
    amb = spec.ambiguous_claims[0]
    interps = {b.interpretation for b in amb.branches}
    assert interps == {"return 0", "raise ValueError"}


def test_parse_json_loose_fenced_and_prose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a": [1,2]} done') == {"a": [1, 2]}
