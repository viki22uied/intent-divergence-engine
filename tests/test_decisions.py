import json

import pytest

from intent_ide.decisions import (
    DecisionsError,
    apply_decisions,
    load_decisions,
    write_decisions_template,
)
from intent_ide.models import IntentSpec


def make_spec() -> IntentSpec:
    return IntentSpec.build("task", [
        {"type": "edge_case", "text": "empty input behavior unstated",
         "source": "inferred", "confidence": 0.7, "ambiguous": True,
         "branches": [{"interpretation": "return 0"},
                      {"interpretation": "raise ValueError"}]},
        {"type": "postcondition", "text": "plain claim",
         "source": "stated", "confidence": 1.0},
    ])


def test_apply_decision_resolves_claim():
    spec = make_spec()
    new_spec, notes = apply_decisions(spec, {"C1": "C1.b"})
    resolved = new_spec.claims[0]
    assert not resolved.ambiguous
    assert "RESOLVED by developer decision" in resolved.text
    assert "raise ValueError" in resolved.text
    assert any("resolved to C1.b" in n for n in notes)
    assert new_spec.version == spec.version + 1


def test_no_decision_keeps_ambiguity():
    spec = make_spec()
    new_spec, notes = apply_decisions(spec, {})
    assert new_spec.ambiguous_claims[0].id == "C1"
    assert any("still ambiguous" in n for n in notes)
    assert new_spec.version == spec.version


def test_unknown_branch_rejected():
    with pytest.raises(DecisionsError):
        apply_decisions(make_spec(), {"C1": "C1.z"})


def test_load_validates_shape(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"C1": "C1.a"}), encoding="utf-8")
    assert load_decisions(p) == {"C1": "C1.a"}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(DecisionsError):
        load_decisions(bad)


def test_template_lists_branches(tmp_path):
    spec = make_spec()
    out = tmp_path / "decisions.json"
    write_decisions_template(spec, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {"C1": "C1.a"}
