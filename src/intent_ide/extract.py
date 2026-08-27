"""Stage 1 - Intent Extraction (R1.1-R1.4)."""
from __future__ import annotations

import json
from pathlib import Path

from .llm import LLMClient
from .models import AMBIGUITY_CHECKLIST, IntentSpec
from .prompts import EXTRACTION_SYSTEM, extraction_user_prompt


class ExtractionError(RuntimeError):
    pass


def _validate_payload(payload: dict | list) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise ExtractionError("Extraction output missing 'claims' list.")
    raw_claims = []
    for rc in payload["claims"]:
        if not isinstance(rc, dict):
            continue
        text = rc.get("text", "").strip()
        ctype = rc.get("type")
        if not text or ctype not in ("precondition", "postcondition", "invariant", "edge_case"):
            continue
        branches = [
            {"interpretation": b.get("interpretation", "").strip()}
            for b in rc.get("branches", [])
            if isinstance(b, dict) and b.get("interpretation", "").strip()
        ]
        rc = dict(rc)
        rc["text"] = text
        rc["branches"] = branches
        raw_claims.append(rc)
    if not raw_claims:
        raise ExtractionError("Extraction produced no valid claims.")
    return raw_claims


def extract_intent(
    llm: LLMClient,
    task_description: str,
    max_claims: int = 25,
    max_task_chars: int = 8000,
) -> tuple[IntentSpec, dict]:
    truncated = False
    original_len = len(task_description)
    if len(task_description) > max_task_chars:
        task_description = task_description[:max_task_chars] + f"\n\n[truncated: original {original_len} chars, showing first {max_task_chars}]"
        truncated = True
    payload = llm.complete_json(EXTRACTION_SYSTEM, extraction_user_prompt(task_description))[0]
    raw_claims = _validate_payload(payload)
    dropped = 0
    if len(raw_claims) > max_claims:
        dropped = len(raw_claims) - max_claims
        raw_claims = raw_claims[:max_claims]
    spec = IntentSpec.build(task_description, raw_claims)
    usage_meta = {
        "checklist_run": list(AMBIGUITY_CHECKLIST),
        "claim_count": len(spec.claims),
        "ambiguous_count": len(spec.ambiguous_claims),
        "truncated_task": truncated,
        "original_task_chars": original_len,
        "claims_dropped_by_cap": dropped,
    }
    return spec, usage_meta


def save_spec(spec: IntentSpec, artifacts_dir: Path) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"intentspec.v{spec.version}.json"
    path.write_text(spec.to_json(), encoding="utf-8")
    return path


def load_spec(path: Path) -> IntentSpec:
    return IntentSpec.from_json(path.read_text(encoding="utf-8"))
