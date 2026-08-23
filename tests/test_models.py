import pytest

from intent_ide.llm import FakeLLMClient
from intent_ide.models import AMBIGUITY_CHECKLIST, Claim, ClaimType, IntentSpec

EXTRACT_PAYLOAD = """
{
  "claims": [
    {"type": "postcondition", "text": "returns sorted list", "source": "stated",
     "confidence": 1.0, "ambiguous": false, "branches": []},
    {"type": "edge_case", "text": "duplicate elements handling is unclear",
     "source": "inferred", "confidence": 0.8, "ambiguous": true,
     "branches": [
       {"interpretation": "duplicates removed"},
       {"interpretation": "duplicates kept"}
     ]}
  ]
}
"""


def make_spec() -> IntentSpec:
    return IntentSpec.build("sort a list", [
        {"type": "postcondition", "text": "returns sorted list",
         "source": "stated", "confidence": 1.0},
        {"type": "edge_case", "text": "duplicate handling unclear",
         "source": "inferred", "confidence": 0.8, "ambiguous": True,
         "branches": [{"interpretation": "removed"}, {"interpretation": "kept"}]},
    ])


def test_roundtrip_json():
    spec = make_spec()
    parsed = IntentSpec.from_json(spec.to_json())
    assert [c.text for c in parsed.claims] == [c.text for c in spec.claims]
    assert parsed.claims[0].source.value == "stated"
    assert parsed.claims[0].confidence == 1.0


def test_ambiguity_preserved_not_resolved():
    spec = make_spec()
    amb = spec.ambiguous_claims[0]
    assert amb.ambiguous is True
    assert len(amb.branches) == 2
    assert amb.branches[0].branch_id == "C2.a"
    assert amb.branches[1].branch_id == "C2.b"


def test_checklist_is_complete():
    assert set(AMBIGUITY_CHECKLIST) == {
        "ordering_guarantees", "duplicate_handling", "empty_and_null_input",
        "boundary_off_by_one", "concurrency_idempotency", "error_handling",
    }


def test_fake_llm_marker_routing():
    llm = FakeLLMClient({"intent-extraction": EXTRACT_PAYLOAD})
    resp = llm.complete("You are an intent-extraction engine.", "task: sort")
    assert '"claims"' in resp.text


def test_invalid_claim_type_rejected():
    with pytest.raises(ValueError):
        IntentSpec.build("t", [{"type": "bogus", "text": "x", "source": "stated"}])
