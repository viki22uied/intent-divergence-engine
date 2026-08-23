"""Benchmark harness for success metrics M1/M2/M3.

Each case directory contains:
  task.txt        - the intent description
  under_test.py   - code containing a seeded "almost right" defect
  labels.json     - {"expect_divergence": bool,
                     "defect_keywords": ["..."],      # claim-text keywords that SHOULD fail
                     "ambiguous_expected": ["C2"]}    # claims expected to be flagged ambiguous
  payloads/       - offline mode: canned LLM replies named C1.txt, C2.a.txt, ...

Metrics produced:
  M2 recall        - fraction of cases with expect_divergence where the engine
                     flagged a divergence matching a defect keyword.
  M3 proxy         - fraction of expected ambiguities actually flagged.
  M1 support       - per-finding CSV for human precision review.

Usage:
  python benchmark/run_benchmark.py benchmark/cases --offline
  python benchmark/run_benchmark.py benchmark/cases            # uses live LLM env
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from intent_ide.config import Config                      # noqa: E402
from intent_ide.llm import FakeLLMClient                  # noqa: E402
from intent_ide.pipeline import run_pipeline              # noqa: E402


class CaseLLM(FakeLLMClient):
    """Routes extraction to case/task_payload.txt and synthesis to payloads/<id>.txt."""

    def __init__(self, case_dir: Path):
        super().__init__({})
        self.case_dir = case_dir
        self.extraction_payload = (case_dir / "task_payload.txt").read_text(encoding="utf-8")

    def complete(self, system, user):
        from intent_ide.llm import LLMResponse
        self.calls.append((system, user))
        if "intent-extraction" in system:
            return LLMResponse(text=self.extraction_payload)
        for payload_file in sorted((self.case_dir / "payloads").glob("*.txt")):
            marker = f'"id": "{payload_file.stem}"'
            if marker in user:
                return LLMResponse(text=payload_file.read_text(encoding="utf-8"))
        raise AssertionError(f"No payload for synthesis call in {self.case_dir.name}")


def run_case(case_dir: Path, offline: bool) -> dict:
    task = (case_dir / "task.txt").read_text(encoding="utf-8")
    labels = json.loads((case_dir / "labels.json").read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix=f"ide_bm_{case_dir.name}_"))
    project = tmp / "proj"
    project.mkdir()
    (project / "under_test.py").write_text(
        (case_dir / "under_test.py").read_text(encoding="utf-8"), encoding="utf-8")

    if offline:
        llm = CaseLLM(case_dir)
    else:
        from intent_ide.llm import make_client
        llm = make_client(Config.from_env())

    result = run_pipeline(llm, task, project, tmp / ".ide", cfg=Config())

    failed_claims = {
        r.claim_id for run in result.runs for r in run.results if r.outcome == "failed"
    }
    spec = result.spec
    failed_texts = [c.text.lower() for c in (spec.claims if spec else []) if c.id in failed_claims]

    caught_keywords = [
        kw for kw in labels.get("defect_keywords", [])
        if any(kw.lower() in t for t in failed_texts)
    ]
    missed_keywords = [
        kw for kw in labels.get("defect_keywords", []) if kw not in caught_keywords
    ]
    flagged_ambig = {c.id for c in (spec.claims if spec else []) if c.ambiguous}
    expected_ambig = set(labels.get("ambiguous_expected", []))

    divergent_detected = bool(failed_claims)
    expected_divergence = labels.get("expect_divergence", False)
    hit = divergent_detected == expected_divergence and (
        not expected_divergence or not missed_keywords
    )

    return {
        "case": case_dir.name,
        "hit": hit,
        "expect_divergence": expected_divergence,
        "divergence_detected": divergent_detected,
        "caught_keywords": caught_keywords,
        "missed_keywords": missed_keywords,
        "ambig_expected": sorted(expected_ambig),
        "ambig_flagged": sorted(flagged_ambig & expected_ambig) if expected_ambig else [],
        "ambig_missed": sorted(expected_ambig - flagged_ambig),
        "false_ambig_flags": sorted(
            c.id for c in (spec.claims if spec else [])
            if c.ambiguous and c.id not in expected_ambig),
        "findings": [
            {"claim_id": c.id, "text": c.text[:200]}
            for c in (spec.claims if spec else []) if c.id in failed_claims
        ],
    }


def aggregate(results: list[dict]) -> dict:
    div_cases = [r for r in results if r["expect_divergence"]]
    clean_cases = [r for r in results if not r["expect_divergence"]]
    recall_hits = sum(1 for r in div_cases
                      if r["divergence_detected"] and not r["missed_keywords"])
    m2_recall = recall_hits / len(div_cases) if div_cases else None
    false_positives = sum(1 for r in clean_cases if r["divergence_detected"])
    amb_expected = sum(len(r["ambig_expected"]) for r in results)
    amb_caught = sum(len(r["ambig_flagged"]) for r in results)
    m3_proxy = amb_caught / amb_expected if amb_expected else None
    over_flags = sum(len(r["false_ambig_flags"]) for r in results)
    return {
        "cases": len(results),
        "M2_recall": round(m2_recall, 3) if m2_recall is not None else "n/a",
        "M1_false_positive_cases": false_positives,
        "M3_ambiguity_caught": f"{amb_caught}/{amb_expected}",
        "M3_overflagged_claims": over_flags,
        "overall_hits": sum(1 for r in results if r["hit"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases_dir")
    ap.add_argument("--offline", action="store_true",
                    help="use canned payloads in each case dir (no API key)")
    args = ap.parse_args()

    case_dirs = sorted(d for d in Path(args.cases_dir).iterdir() if d.is_dir())
    results = [run_case(d, args.offline) for d in case_dirs]

    summary = aggregate(results)
    print(json.dumps(summary, indent=2))
    out_csv = Path(args.cases_dir) / "findings_for_review.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "claim_id", "finding_text", "hit"])
        for r in results:
            for finding in r["findings"]:
                writer.writerow([r["case"], finding["claim_id"], finding["text"], r["hit"]])
    print(f"\nPer-finding rows for human M1 review: {out_csv}", file=sys.stderr)
    detail = Path(args.cases_dir) / "last_results.json"
    detail.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
