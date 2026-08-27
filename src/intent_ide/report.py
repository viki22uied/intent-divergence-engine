"""Stage 4 - Divergence Reporting (R4.1-R4.4)."""
from __future__ import annotations

import re

from .config import Config
from .coverage import CoverageSummary, compute_coverage
from .execute import SuiteRun, TestResult
from .models import IntentSpec


def _escape_inline(s: str) -> str:
    # break out of `...` inline code — neutralize backticks and pipes
    return s.replace("`", "'").replace("|", "\\|").replace("\n", " ")[:400]


def _escape_block(s: str) -> str:
    # prevent breaking out of ``` fences
    return s.replace("```", "'''")[:2000]

PROHIBITED_PHRASES = [
    "verified correct",
    "proven correct",
    "fully verified",
    "fully correct",
    "guaranteed correct",
]


def scrub_prohibited_language(report_text: str) -> str:
    lowered = report_text.lower()
    for phrase in PROHIBITED_PHRASES:
        if phrase in lowered:
            idx = lowered.find(phrase)
            report_text = (
                report_text[:idx]
                + "passed all generated checks"
                + report_text[idx + len(phrase):]
            )
            lowered = report_text.lower()
    return report_text


def _divergent_entry_md(r: TestResult, spec: IntentSpec) -> str:
    claim = next((c for c in spec.claims if c.id == r.claim_id), None)
    label = _escape_inline(r.branch_id or r.claim_id or "unknown")
    claim_text = _escape_inline(claim.text if claim else "(claim text unavailable)")
    if r.actual:
        actual_txt = "`" + _escape_inline(r.actual.splitlines()[0][:300]) + "`"
    elif r.message:
        actual_txt = "`" + _escape_inline(r.message.splitlines()[-1][:300]) + "`"
    else:
        actual_txt = "(see failure output)"
    failing = _escape_inline(r.failing_input) if r.failing_input else "see test output"
    expected = _escape_inline(r.expected) if r.expected else "(per claim above)"
    # pick a fence longer than any run in the message (Finding 7)
    fence = "```"
    if r.message and "```" in r.message:
        # find longest backtick run and go one longer
        longest = max((len(m.group(0)) for m in re.finditer(r"`+", r.message)), default=3)
        fence = "`" * (longest + 1)
        # but also neutralize the exact fence string inside
    block_content = _escape_block(r.message[:1500]) if r.message else ""
    lines = [
        f"### Divergence on `{label}`",
        f"- **Claim violated:** {claim_text}",
        f"- **Input:** `{failing}`",
        f"- **Actual behavior:** {actual_txt}",
        f"- **Expected behavior (per intent):** {expected}",
        "",
        "<details><summary>Raw failure output</summary>",
        "",
        fence,
        block_content,
        fence,
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def _ambiguous_section_md(spec: IntentSpec) -> str:
    ambiguous = spec.ambiguous_claims
    if not ambiguous:
        return ""
    lines = ["## Unresolved / Ambiguous — your decision is required", ""]
    for claim in ambiguous:
        lines.append(f"### {_escape_inline(claim.id)}: {_escape_inline(claim.text)}")
        lines.append("")
        lines.append("| Branch | Interpretation |")
        lines.append("|---|---|")
        for b in claim.branches:
            interp = _escape_inline(b.interpretation)
            lines.append(f"| `{_escape_inline(b.branch_id)}` | {interp} |")
        lines.append("")
        lines.append(
            "These readings conflict. The engine did **not** pick one. Tests were "
            "generated per branch so you can see exactly where behavior differs. "
            "Resolve before treating this area as covered."
        )
        lines.append("")
    return "\n".join(lines)


def build_report(
    spec: IntentSpec,
    runs: list[SuiteRun],
    edge_categories_targeted: list[str],
    edge_categories_left_to_fuzz: list[str],
    cfg: Config | None = None,
    system_errors: list[str] | None = None,
    coverage_extra_warnings: list[str] | None = None,
    resource_note: str = "",
    cost_usd: float | None = None,
) -> str:
    cfg = cfg or Config()
    coverage = compute_coverage(spec, runs)
    divergent = [
        r for run in runs for r in run.results
        if r.outcome == "failed"
    ]
    passed = [r for run in runs for r in run.results if r.outcome == "passed"]
    errored = [r for run in runs for r in run.results if r.outcome in ("error", "timeout")]

    parts = ["# Intent Divergence Report", ""]

    parts.append(coverage.to_markdown())
    for warning in coverage_extra_warnings or []:
        parts.append("")
        parts.append(f"> ⚠ **Coverage stability:** {warning}")
    parts.append("")
    parts.append("---")
    parts.append("")

    if system_errors:
        parts.append("## System Errors (engine failures — NOT code findings)")
        for err in system_errors:
            parts.append(f"- {err}")
        parts.append("")
        parts.append(
            "> A pipeline failure is never reported as a code defect and never "
            "blocks this PR by itself. Re-run to confirm."
        )
        parts.append("")

    parts.append(f"## Confirmed Divergent ({len(divergent)})")
    if divergent:
        for r in divergent:
            parts.append(_divergent_entry_md(r, spec))
    else:
        parts.append("_No confirmed divergences found._")
    parts.append("")

    amb = _ambiguous_section_md(spec)
    if amb:
        parts.append(amb)

    parts.append(f"## Confirmed Correct ({len(passed)})")
    if passed:
        for r in passed:
            label = r.branch_id or r.claim_id or r.function_name
            parts.append(f"- `{label}` ({r.function_name}): behavior matched intent on generated inputs.")
    else:
        parts.append("_No claims passed._")
    parts.append("")

    if errored or runs and any(run.timed_out for run in runs):
        parts.append(f"## Execution Errors ({len(errored)})")
        for r in errored:
            parts.append(f"- `{r.function_name}`: {r.message[:200]}")
        parts.append("")

    parts.append("## Method Notes")
    parts.append(
        f"- Edge-case categories targeted by synthesis: "
        f"{', '.join(edge_categories_targeted) or 'none recorded'}."
    )
    fuzz_only = [c for c in ("ordering_guarantees", "duplicate_handling",
                             "empty_and_null_input", "boundary_off_by_one",
                             "concurrency_idempotency", "error_handling")
                 if c not in edge_categories_targeted]
    parts.append(f"- Left to general-purpose fuzzing: {', '.join(fuzz_only)}.")
    parts.append(f"- Caps: max {cfg.max_tests_per_claim} tests/claim, "
                 f"{cfg.max_hypothesis_examples} hypothesis examples, "
                 f"{cfg.suite_timeout_seconds}s suite timeout.")
    if resource_note:
        parts.append(f"- Execution resources (R3.4 log): {resource_note}.")
    if cost_usd is not None:
        parts.append(f"- Estimated LLM cost this run: ${cost_usd:.4f}.")
    parts.append("- Hypothesis seeds are pinned per run and logged in "
                 "run_meta.json; re-runs are replayable (R9.3).")
    parts.append("- These findings reflect only what was tested. Untested behavior "
                 "is unknown, not safe.")
    parts.append("")
    parts.append("*This report does not verify correctness. It reports which stated "
                 "intent claims were checked and what happened.*")

    return scrub_prohibited_language("\n".join(parts))
