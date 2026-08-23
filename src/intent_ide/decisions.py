"""Developer ambiguity decisions (closes the R4.2 loop).

A decisions file maps claim IDs to the branch chosen by a human:
    {"C2": "C2.b"}
Applying decisions converts an ambiguous claim into one resolved claim whose
text records both the decision and who made it. Undecided claims stay
ambiguous and keep generating per-branch tests.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Claim, IntentSpec


class DecisionsError(ValueError):
    pass


def load_decisions(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DecisionsError("decisions file must be a JSON object of {claim_id: branch_id}")
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise DecisionsError(f"bad decision entry: {k!r}: {v!r}")
    return raw


def apply_decisions(spec: IntentSpec, decisions: dict[str, str]) -> tuple[IntentSpec, list[str]]:
    """Return (new_spec, notes). Mutates nothing."""
    notes: list[str] = []
    new_claims: list[Claim] = []
    for claim in spec.claims:
        chosen = decisions.get(claim.id)
        if not (claim.ambiguous and claim.branches):
            new_claims.append(claim)
            continue
        branch = next((b for b in claim.branches if b.branch_id == chosen), None)
        if branch is None:
            valid = ", ".join(b.branch_id for b in claim.branches)
            if chosen is not None:
                raise DecisionsError(
                    f"{claim.id}: unknown branch {chosen!r} (valid: {valid})")
            notes.append(f"{claim.id}: still ambiguous — no decision provided; "
                         f"branches remain {valid}")
            new_claims.append(claim)
            continue
        new_claims.append(Claim(
            id=claim.id,
            type=claim.type,
            text=f"[RESOLVED by developer decision: {branch.branch_id}] "
                 f"Interpretation adopted: {branch.interpretation}. "
                 f"Original claim: {claim.text}",
            source=claim.source,
            confidence=1.0,
            ambiguous=False,
            branches=[],
        ))
        notes.append(f"{claim.id}: resolved to {branch.branch_id}")
    return IntentSpec(
        version=spec.version,
        task_description_hash=spec.task_description_hash,
        claims=new_claims,
        checklist_run=spec.checklist_run,
        spec_id=spec.spec_id,
    ), notes


def write_decisions_template(spec: IntentSpec, path: Path) -> None:
    template = {
        c.id: c.branches[0].branch_id if c.branches else ""
        for c in spec.claims if c.ambiguous
    }
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")
