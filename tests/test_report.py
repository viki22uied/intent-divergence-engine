from intent_ide.config import Config
from intent_ide.coverage import compute_coverage
from intent_ide.execute import SuiteRun, TestResult
from intent_ide.models import IntentSpec
from intent_ide.report import build_report, scrub_prohibited_language


def make_spec() -> IntentSpec:
    return IntentSpec.build("task", [
        {"type": "postcondition", "text": "sums positives",
         "source": "stated", "confidence": 1.0},
        {"type": "edge_case", "text": "empty input behavior unstated",
         "source": "inferred", "confidence": 0.7, "ambiguous": True,
         "branches": [{"interpretation": "return 0"},
                      {"interpretation": "raise ValueError"}]},
        {"type": "invariant", "text": "requires external payment API",
         "source": "inferred", "confidence": 0.5,
         "untestable_reason": "requires live third-party API not available in sandbox"},
    ])


def runs_with(passed_ids=(), failed_ids=()):
    results = [TestResult(test_id=f, function_name=f.replace(".", "_"), outcome="passed", claim_id=c)
               for c, f in passed_ids]
    results += [TestResult(
        test_id=f, function_name=f.replace(".", "_"), outcome="failed", claim_id=c,
        message='AssertionError: INPUT: [-1]\nEXPECTED: 0\nACTUAL: -1',
        failing_input="[-1]", expected="0", actual="-1")
        for c, f in failed_ids]
    return [SuiteRun(results=results, duration_seconds=1.2)]


def test_report_coverage_first():
    report = build_report(make_spec(), runs_with(passed_ids=[("C1", "t_C1")]),
                          edge_categories_targeted=["empty_and_null_input"],
                          edge_categories_left_to_fuzz=[], cfg=Config())
    cov_pos = report.find("Coverage Summary")
    div_pos = report.find("Confirmed Divergent")
    correct_pos = report.find("Confirmed Correct")
    assert 0 < cov_pos < div_pos < correct_pos


def test_no_verified_correct_language():
    spec = make_spec()
    runs = runs_with(passed_ids=[("C1", "t_C1")])
    report = build_report(spec, runs, [], [], Config())
    lowered = report.lower()
    for phrase in ["verified correct", "proven correct"]:
        assert phrase not in lowered
    scrubbed = scrub_prohibited_language("The code is verified correct.")
    assert "verified correct" not in scrubbed.lower()


def test_divergent_entry_has_required_fields():
    report = build_report(make_spec(), runs_with(failed_ids=[("C1", "t_C1")]),
                          [], [], Config())
    assert "Claim violated" in report
    assert "Input:" in report
    assert "Actual behavior" in report
    assert "Expected behavior" in report


def test_ambiguity_shown_without_default_answer():
    report = build_report(make_spec(), runs_with(), [], [], Config())
    assert "Unresolved / Ambiguous" in report
    assert "return 0" in report and "raise ValueError" in report
    assert "did **not** pick one" in report


def test_untestable_claim_listed_with_reason():
    report = build_report(make_spec(), runs_with(), [], [], Config())
    assert "Untested claims" in report
    assert "third-party API" in report


def test_coverage_fraction():
    coverage = compute_coverage(make_spec(), runs_with(passed_ids=[("C1", "t_C1")]))
    assert coverage.total == 3
    assert coverage.tested_count == 1
    assert coverage.fraction() == "1/3"


def test_system_errors_distinct_from_findings():
    report = build_report(make_spec(), runs_with(),
                          [], [], Config(),
                          system_errors=["sandbox crashed: boom"])
    assert "System Errors" in report
    assert "NOT a finding about your code" not in report
    assert "engine failures" in report
