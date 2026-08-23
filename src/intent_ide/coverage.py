"""Stage 5 - Coverage and Confidence Reporting (R5.1-R5.3) + stability (M5)."""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .execute import SuiteRun
from .models import Claim, IntentSpec


@dataclass
class ClaimCoverage:
    claim: Claim
    tested: bool
    reason: str | None = None


@dataclass
class CoverageSummary:
    entries: list[ClaimCoverage] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def tested_count(self) -> int:
        return sum(1 for e in self.entries if e.tested)

    def fraction(self) -> str:
        if self.total == 0:
            return "0/0"
        return f"{self.tested_count}/{self.total}"

    def to_markdown(self) -> str:
        lines = [
            "## Coverage Summary",
            f"**Claims tested: {self.fraction()}** "
            "(a passing suite with low coverage is weak evidence — read the gaps first)",
            "",
            "| Claim | Type | Tested | Note |",
            "|---|---|---|---|",
        ]
        for e in self.entries:
            status = "yes" if e.tested else "**no**"
            note = e.reason or ""
            text = e.claim.text.replace("|", "\\|")
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"| {e.claim.id} | {e.claim.type.value} | {status} | {note or text} |")
        untested = [e for e in self.entries if not e.tested]
        if untested:
            lines.append("")
            lines.append("### Untested claims (R5.2)")
            for e in untested:
                lines.append(f"- **{e.claim.id}**: {e.reason}")
        return "\n".join(lines)


def compute_coverage(spec: IntentSpec, runs: list[SuiteRun]) -> CoverageSummary:
    executed_claims: dict[str, list[str]] = {}
    errored_claims: set[str] = set()
    for run in runs:
        for r in run.results:
            if r.claim_id:
                if r.outcome in ("passed", "failed"):
                    executed_claims.setdefault(r.claim_id, []).append(r.outcome)
                elif r.outcome in ("error", "timeout"):
                    errored_claims.add(r.claim_id)

    summary = CoverageSummary()
    generated_claim_ids = {r.claim_id for run in runs for r in run.results if r.claim_id}
    for claim in spec.claims:
        target_ids = {claim.id} | {b.branch_id for b in claim.branches}
        has_tests = any(tid in generated_claim_ids for tid in target_ids)
        outcomes = []
        for tid in target_ids:
            outcomes.extend(executed_claims.get(tid, []))
        if outcomes:
            summary.entries.append(ClaimCoverage(claim=claim, tested=True))
        elif claim.id in errored_claims:
            summary.entries.append(ClaimCoverage(
                claim=claim, tested=False,
                reason="test generation or execution errored; no verdict possible"))
        elif not claim.testable:
            summary.entries.append(ClaimCoverage(
                claim=claim, tested=False,
                reason=f"untestable as stated: {claim.untestable_reason}"))
        elif has_tests:
            summary.entries.append(ClaimCoverage(
                claim=claim, tested=False,
                reason="tests were generated but produced no executable result "
                       "(possible timeout or collection error)"))
        else:
            summary.entries.append(ClaimCoverage(
                claim=claim, tested=False,
                reason="no test could be synthesized for this claim"))
    return summary


def load_history(out_dir: Path) -> list[dict]:
    path = out_dir / "coverage_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def record_and_check_stability(
    out_dir: Path,
    spec: IntentSpec,
    summary: CoverageSummary,
) -> dict:
    """M5: append this run to coverage history and flag silent drops.

    A drop is 'silent' when coverage falls on the same task hash without an
    explicit logged reason. Returns {"dropped": bool, "previous": int|None,
    "warning": str|None}.
    """
    history = load_history(out_dir)
    prior = [h for h in history if h.get("task_hash") == spec.task_description_hash]
    previous = prior[-1] if prior else None
    entry = {
        "task_hash": spec.task_description_hash,
        "tested": summary.tested_count,
        "total": summary.total,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    history.append(entry)
    (out_dir / "coverage_history.json").write_text(
        json.dumps(history, indent=2), encoding="utf-8")

    result: dict = {"dropped": False, "previous": None, "warning": None}
    if previous is not None:
        prev_fraction = (
            previous["tested"] / previous["total"] if previous.get("total") else 0
        )
        cur_fraction = summary.tested_count / summary.total if summary.total else 0
        if cur_fraction < prev_fraction:
            reason = entry["recorded_at"]
            result.update({
                "dropped": True,
                "previous": prev_fraction,
                "warning": (
                    f"Coverage dropped from {prev_fraction:.0%} to {cur_fraction:.0%} "
                    f"for the same intent ({spec.task_description_hash}). "
                    f"Logged at {reason}. This should not happen silently — "
                    "investigate before trusting the new pass results."
                ),
            })
    return result
