"""Pipeline orchestration (R9.1, R9.3, R9.5) + decisions, history, replay."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .coverage import compute_coverage, record_and_check_stability
from .decisions import apply_decisions, load_decisions
from .execute import SuiteRun, _sandbox_warning, run_suite
from .extract import extract_intent, load_spec, save_spec
from .llm import LLMClient, estimate_cost_usd
from .models import IntentSpec
from .report import build_report
from .synthesize import TestSuite, synthesize_suite

EXIT_OK = 0
EXIT_DIVERGENCE = 1
EXIT_AMBIGUITY = 2
EXIT_SYSTEM_FAILURE = 3


@dataclass
class PipelineResult:
    exit_code: int
    report_markdown: str
    spec: IntentSpec | None = None
    suite: TestSuite | None = None
    runs: list[SuiteRun] = field(default_factory=list)
    system_errors: list[str] = field(default_factory=list)
    artifacts_dir: Path | None = None
    usage: dict = field(default_factory=dict)


def _claim_map(suite: TestSuite) -> dict[str, tuple[str, str | None]]:
    return {t.function_name: (t.claim_id, t.branch_id) for t in suite.tests}


def _system_failure(stage: str, error: Exception, spec_path: Path | None,
                    out_dir: Path) -> PipelineResult:
    note = f"IntentSpec saved to `{spec_path.name}`." if spec_path else "Nothing was checked."
    return PipelineResult(
        exit_code=EXIT_SYSTEM_FAILURE,
        report_markdown=(
            "# Intent Divergence Report\n\n## System Errors\n"
            f"- {stage} failed: {error}\n\n"
            "The engine failed; this is NOT a finding about your code. "
            f"{note}"
        ),
        system_errors=[f"{stage} failed: {error}"],
        artifacts_dir=out_dir,
    )


def _apply_human_decisions(spec: IntentSpec, decisions_path: Path | None):
    """Returns (spec_after_decisions, decision_notes)."""
    if decisions_path is None or not decisions_path.exists():
        return spec, []
    decisions = load_decisions(decisions_path)
    return apply_decisions(spec, decisions)


def run_pipeline(
    llm: LLMClient,
    task_description: str,
    project_dir: Path,
    out_dir: Path,
    signatures: str = "",
    cfg: Config | None = None,
    decisions_file: Path | None = None,
    seed: int | None = None,
) -> PipelineResult:
    cfg = cfg or Config()
    run_seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_dir = out_dir / "generated_tests"
    errors: list[str] = []

    try:
        spec, extract_meta = extract_intent(
            llm, task_description,
            max_claims=cfg.max_claims,
            max_task_chars=cfg.max_task_chars,
        )
        # surface caps as warnings in report
        if extract_meta.get("claims_dropped_by_cap"):
            errors.append(
                f"extraction: {extract_meta['claims_dropped_by_cap']} claims dropped: exceeds IDE_MAX_CLAIMS={cfg.max_claims} (reported as untested, not silently ignored)"
            )
        if extract_meta.get("truncated_task"):
            errors.append(
                f"task description truncated from {extract_meta['original_task_chars']} to {cfg.max_task_chars} chars (IDE_MAX_TASK_CHARS)"
            )
    except Exception as e:
        return _system_failure("Stage 1 (intent extraction)", e, None, out_dir)
    spec_path = save_spec(spec, out_dir)  # R1.4 (pre-decision version)

    try:
        spec, decision_notes = _apply_human_decisions(spec, decisions_file)
        if decision_notes:
            save_spec(spec, out_dir)
            (out_dir / "decisions_applied.json").write_text(
                json.dumps(decision_notes, indent=2), encoding="utf-8")
    except Exception as e:
        return _system_failure("Decisions application", e, spec_path, out_dir)

    try:
        suite = synthesize_suite(
            llm, spec, signatures, generated_dir,
            cfg.max_tests_per_claim, cfg.max_hypothesis_examples,
            seed=run_seed,
        )
        if not suite.files and not spec.ambiguous_claims:
            raise RuntimeError("no test files could be synthesized for any claim")
    except Exception as e:
        return _system_failure("Stage 2 (test synthesis)", e, spec_path, out_dir)

    claim_map = _claim_map(suite)
    (out_dir / "claim_map.json").write_text(
        json.dumps({k: list(v) for k, v in claim_map.items()}, indent=2),
        encoding="utf-8")

    # surface safety-gate blocks (Finding 1) as system notes
    if suite.safety_blocked:
        errors.append(
            f"safety gate blocked {suite.safety_blocked} generated test(s): "
            + "; ".join(suite.safety_block_reasons[:3])
        )
    runs: list[SuiteRun] = []
    if suite.files:
        try:
            run = run_suite(
                project_dir=project_dir,
                generated_tests_dir=generated_dir,
                claim_map=claim_map,
                suite_timeout_seconds=cfg.suite_timeout_seconds,
                test_timeout_seconds=cfg.test_timeout_seconds,
                memory_limit_mb=cfg.memory_limit_mb,
            )
            runs.append(run)
            # also surface any execute-time blocks
            for r in run.results:
                if "blocked by safety gate" in r.message:
                    errors.append(f"execution safety gate: {r.test_id}: {r.message[:200]}")
            # surface container-isolation warning as a report-visible note, not just stdout
            sw = _sandbox_warning()
            if sw:
                errors.append(f"sandbox: {sw}")
        except Exception as e:
            errors.append(f"execution stage failed: {e}")

    coverage = compute_coverage(spec, runs)
    stability = record_and_check_stability(out_dir, spec, coverage)  # M5

    usage_dict = getattr(llm, "usage", None).to_dict() if hasattr(llm, "usage") else {}
    cost_usd = None
    if usage_dict:
        from .llm import LLMUsage
        cost_usd = estimate_cost_usd(LLMUsage(**{
            "prompt_tokens": usage_dict.get("prompt_tokens", 0),
            "completion_tokens": usage_dict.get("completion_tokens", 0),
        }))

    report_sections = build_report(
        spec, runs,
        edge_categories_targeted=suite.edge_categories_targeted,
        edge_categories_left_to_fuzz=[],
        cfg=cfg,
        system_errors=errors,
        coverage_extra_warnings=(
            [stability["warning"]] if stability["warning"] else []
        ),
        resource_note=_resource_note(runs),
        cost_usd=cost_usd,
    )
    (out_dir / "report.md").write_text(report_sections, encoding="utf-8")

    has_divergence = any(r.outcome == "failed" for run in runs for r in run.results)
    has_ambiguity = bool(spec.ambiguous_claims)
    executed = any(r.outcome in ("failed", "passed") for run in runs for r in run.results)

    meta = {
        "run_seed": run_seed,  # R9.3: injected into hypothesis settings; replayable
        "duration_seconds": round(time.monotonic() - started, 2),
        "llm_usage": usage_dict,
        "estimated_cost_usd": cost_usd,
        "claims_total": coverage.total,
        "claims_tested": coverage.tested_count,
        "tests_generated": len(suite.tests),
        "tests_untraceable_dropped": suite.untraceable_dropped,
        "decisions_applied": bool(decision_notes),
        "coverage_stability_drop": stability["dropped"],
        "exit_condition": (
            "divergence" if has_divergence else
            "ambiguity" if has_ambiguity and not executed else
            "ok"),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    exit_code = EXIT_OK
    if has_divergence:
        exit_code = EXIT_DIVERGENCE
    elif meta["exit_condition"] == "ambiguity":
        exit_code = EXIT_AMBIGUITY

    return PipelineResult(
        exit_code=exit_code,
        report_markdown=report_sections,
        spec=spec,
        suite=suite,
        runs=runs,
        system_errors=errors,
        artifacts_dir=out_dir,
        usage=meta,
    )


def _resource_note(runs: list[SuiteRun]) -> str:
    parts = []
    for i, run in enumerate(runs, start=1):
        mem = f", peak memory {run.peak_memory_mb} MB" if run.peak_memory_mb else ""
        parts.append(f"suite {i}: {run.duration_seconds:.1f}s{mem}")
    return "; ".join(parts)


def replay_run(project_dir: Path, run_dir: Path, cfg: Config | None = None) -> PipelineResult:
    """R9.3 auditability: re-execute a previous run's stored tests + IntentSpec.

    Uses the logged seed (already embedded in generated test files) and the
    saved claim_map.json; no LLM calls are made.
    """
    cfg = cfg or Config()
    specs = sorted(run_dir.glob("intentspec.v*.json"))
    if not specs:
        raise FileNotFoundError(f"No intentspec found in {run_dir}")
    spec = load_spec(specs[-1])
    map_path = run_dir / "claim_map.json"
    if not map_path.exists():
        raise FileNotFoundError(f"No claim_map.json in {run_dir}")
    raw_map = json.loads(map_path.read_text(encoding="utf-8"))
    claim_map = {k: tuple(v) for k, v in raw_map.items()}
    generated_dir = run_dir / "generated_tests"

    run = run_suite(
        project_dir=project_dir,
        generated_tests_dir=generated_dir,
        claim_map=claim_map,
        suite_timeout_seconds=cfg.suite_timeout_seconds,
        test_timeout_seconds=cfg.test_timeout_seconds,
        memory_limit_mb=cfg.memory_limit_mb,
    )
    report = build_report(
        spec, [run],
        edge_categories_targeted=["(replayed run — see original report)"],
        edge_categories_left_to_fuzz=[],
        cfg=cfg,
    )
    header = "> REPLAY of stored run — findings must match the original modulo "
    header += "explicitly randomized seeds (which were pinned at synthesis).\n\n"
    (run_dir / "replay_report.md").write_text(header + report, encoding="utf-8")
    has_divergence = any(r.outcome == "failed" for r in run.results)
    return PipelineResult(
        exit_code=EXIT_DIVERGENCE if has_divergence else EXIT_OK,
        report_markdown=header + report,
        spec=spec,
        runs=[run],
        artifacts_dir=run_dir,
    )
