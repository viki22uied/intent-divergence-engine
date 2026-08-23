import json

from intent_ide.coverage import compute_coverage, record_and_check_stability
from intent_ide.execute import SuiteRun, TestResult
from intent_ide.models import IntentSpec


def make_spec() -> IntentSpec:
    return IntentSpec.build("t", [
        {"type": "postcondition", "text": "claim one", "source": "stated", "confidence": 1.0},
        {"type": "postcondition", "text": "claim two", "source": "stated", "confidence": 1.0},
    ])


def runs_for(spec, passed_ids):
    results = [TestResult(test_id=f"t_{c}", function_name=f"test_t_{c}",
                          outcome="passed", claim_id=c) for c in passed_ids]
    return [SuiteRun(results=results)]


def test_first_run_no_drop(tmp_path):
    spec = make_spec()
    cov = compute_coverage(spec, runs_for(spec, ["C1"]))
    res = record_and_check_stability(tmp_path, spec, cov)
    assert res["dropped"] is False
    assert (tmp_path / "coverage_history.json").exists()


def test_silent_drop_detected(tmp_path):
    spec = make_spec()
    full = compute_coverage(spec, runs_for(spec, ["C1", "C2"]))
    record_and_check_stability(tmp_path, spec, full)
    partial = compute_coverage(spec, runs_for(spec, ["C1"]))
    res = record_and_check_stability(tmp_path, spec, partial)
    assert res["dropped"] is True
    assert res["previous"] == 1.0
    assert "Coverage dropped" in res["warning"]


def test_improvement_not_flagged(tmp_path):
    spec = make_spec()
    record_and_check_stability(tmp_path, spec,
                               compute_coverage(spec, runs_for(spec, ["C1"])))
    res = record_and_check_stability(
        tmp_path, spec, compute_coverage(spec, runs_for(spec, ["C1", "C2"])))
    assert res["dropped"] is False
