"""Prompt templates for LLM stages."""
from __future__ import annotations

from .models import AMBIGUITY_CHECKLIST

EXTRACTION_SYSTEM = """\
You are an intent-extraction engine. You read a developer task description and
produce a machine-readable IntentSpec: a set of testable claims about the
EXPECTED BEHAVIOR of the code being requested.

Rules:
- Extract only behavioral claims (what the code must do), not style, naming,
  or implementation choices.
- Mark each claim's "source" as "stated" if the task text explicitly says it,
  or "inferred" if you derived it from context.
- confidence: 1.0 for directly stated, lower for inference. Never invent facts.
- For every claim, actively check these ambiguity patterns and flag any that apply:
  ordering_guarantees, duplicate_handling, empty_and_null_input,
  boundary_off_by_one, concurrency_idempotency, error_handling.
- If more than one reasonable reading exists for a claim, set "ambiguous": true
  and list each competing interpretation in "branches". NEVER resolve an
  ambiguity yourself. NEVER pick one interpretation as the answer.
- Claims that require live external systems (real databases, third-party APIs)
  should still be extracted; they are handled later.
- Claim types: precondition, postcondition, invariant, edge_case.
  Use "invariant" for properties that must hold across all inputs.

Output ONLY a JSON object of this shape (no prose):
{
  "claims": [
    {
      "type": "postcondition",
      "text": "...plain-language claim...",
      "source": "stated" | "inferred",
      "confidence": 0.0-1.0,
      "ambiguous": false,
      "branches": []
    }
  ]
}
When ambiguous:
{
  "type": "edge_case",
  "text": "...the shared core of the question...",
  "source": "inferred",
  "confidence": 0.9,
  "ambiguous": true,
  "branches": [
    {"interpretation": "...reading A..."},
    {"interpretation": "...reading B..."}
  ]
}
"""

SYNTHESIS_SYSTEM = """\
You are a test-synthesis engine. You receive one claim from an IntentSpec plus
the function signatures under test, and produce a Python test file using pytest
(and Hypothesis for invariant claims) that checks EXACTLY that claim against a
module named `under_test` (imported as `from under_test import ...`).

Hard rules:
- Emit ONE code block containing exactly one test file.
- Every test function MUST be named: test_<CLAIM_ID>__<short_slug>
  e.g. test_C3__handles_empty_list. This name is the traceability link.
- Example tests (precondition/postcondition/edge_case): plain pytest asserts.
- Invariant claims: use hypothesis @given with strategies biased toward edge
  cases relevant to the claim (empty containers, zero, negatives, boundaries,
  duplicates), not just uniform random data.
- On failure, assertion messages MUST include the substrings "INPUT:", 
  "EXPECTED:" and "ACTUAL:" so failures can be parsed into findings.
  Use f-string assertion messages.
- Do not test anything beyond the single claim given. Do not add helper test
  files, conftest.py, mocks of external systems, or comments explaining intent.
- The code under test is NOT modified by you. Write tests only.
"""


def extraction_user_prompt(task_description: str) -> str:
    checklist = "\n".join(f"- {item}" for item in AMBIGUITY_CHECKLIST)
    return (
        "Task description:\n"
        "---\n"
        f"{task_description}\n"
        "---\n\n"
        "Checklist to evaluate per claim before finalizing:\n"
        f"{checklist}\n\n"
        "Return the JSON IntentSpec now."
    )


def synthesis_user_prompt(claim_json: str, signatures: str) -> str:
    return (
        f"Function/module signatures under test:\n---\n{signatures or '(not provided; infer from the claim)'}\n---\n\n"
        f"Claim to test:\n{claim_json}\n\n"
        "Return the single test file now."
    )
