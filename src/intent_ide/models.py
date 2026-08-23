"""IntentSpec data model (Stage 1 output contract, R1.x)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class ClaimType(str, Enum):
    PRECONDITION = "precondition"
    POSTCONDITION = "postcondition"
    INVARIANT = "invariant"
    EDGE_CASE = "edge_case"


class ClaimSource(str, Enum):
    STATED = "stated"
    INFERRED = "inferred"


@dataclass
class AmbiguityBranch:
    branch_id: str
    interpretation: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AmbiguityBranch":
        return cls(branch_id=d["branch_id"], interpretation=d["interpretation"])


@dataclass
class Claim:
    id: str
    type: ClaimType
    text: str
    source: ClaimSource
    confidence: float
    ambiguous: bool = False
    branches: list[AmbiguityBranch] = field(default_factory=list)
    untestable_reason: Optional[str] = None

    @property
    def testable(self) -> bool:
        return self.untestable_reason is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "text": self.text,
            "source": self.source.value,
            "confidence": self.confidence,
            "ambiguous": self.ambiguous,
            "branches": [b.to_dict() for b in self.branches],
            "untestable_reason": self.untestable_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        return cls(
            id=d["id"],
            type=ClaimType(d["type"]),
            text=d["text"],
            source=ClaimSource(d["source"]),
            confidence=float(d.get("confidence", 0.5)),
            ambiguous=bool(d.get("ambiguous", False)),
            branches=[AmbiguityBranch.from_dict(b) for b in d.get("branches", [])],
            untestable_reason=d.get("untestable_reason"),
        )


AMBIGUITY_CHECKLIST = [
    "ordering_guarantees",
    "duplicate_handling",
    "empty_and_null_input",
    "boundary_off_by_one",
    "concurrency_idempotency",
    "error_handling",
]


def _branch_id(claim_id: str, index: int) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz"
    suffix = letters[index] if index < len(letters) else str(index)
    return f"{claim_id}.{suffix}"


@dataclass
class IntentSpec:
    version: int
    task_description_hash: str
    claims: list[Claim]
    checklist_run: list[str] = field(default_factory=lambda: list(AMBIGUITY_CHECKLIST))
    spec_id: Optional[str] = None

    @property
    def ambiguous_claims(self) -> list[Claim]:
        return [c for c in self.claims if c.ambiguous and c.branches]

    def to_json(self) -> str:
        return json.dumps(
            {
                "spec_version": self.version,
                "task_description_hash": self.task_description_hash,
                "checklist_run": self.checklist_run,
                "claims": [c.to_dict() for c in self.claims],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> "IntentSpec":
        d = json.loads(raw)
        return cls(
            version=int(d["spec_version"]),
            task_description_hash=d["task_description_hash"],
            claims=[Claim.from_dict(c) for c in d["claims"]],
            checklist_run=d.get("checklist_run", list(AMBIGUITY_CHECKLIST)),
        )

    @classmethod
    def build(cls, task_description: str, raw_claims: list[dict]) -> "IntentSpec":
        claims: list[Claim] = []
        for i, rc in enumerate(raw_claims, start=1):
            cid = f"C{i}"
            ctype = ClaimType(rc["type"])
            if not isinstance(ctype, ClaimType):
                raise ValueError(f"invalid claim type: {rc.get('type')}")
            branches = [
                AmbiguityBranch(branch_id=_branch_id(cid, j), interpretation=b["interpretation"])
                for j, b in enumerate(rc.get("branches", []))
            ]
            ambiguous = bool(rc.get("ambiguous")) and len(branches) >= 2
            source_raw = rc.get("source", "inferred")
            source = ClaimSource(source_raw)
            confidence = float(rc.get("confidence", 0.5))
            claims.append(
                Claim(
                    id=cid,
                    type=ctype,
                    text=rc["text"].strip(),
                    source=source,
                    confidence=max(0.0, min(1.0, confidence)),
                    ambiguous=ambiguous,
                    branches=branches if ambiguous else [],
                    untestable_reason=rc.get("untestable_reason"),
                )
            )
        digest = hashlib.sha256(task_description.encode("utf-8")).hexdigest()[:16]
        return cls(version=1, task_description_hash=digest, claims=claims)
