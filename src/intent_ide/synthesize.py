"""Stage 2 - Test Synthesis (R2.1-R2.5)."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import LLMClient
from .models import AmbiguityBranch, Claim, IntentSpec
from .prompts import SYNTHESIS_SYSTEM, synthesis_user_prompt


class SynthesisError(RuntimeError):
    pass


TEST_NAME_RE = re.compile(r"^test_([A-Za-z]\w*)__([a-z0-9_]+)$")
EDGE_CATEGORIES = [
    "empty_and_null_input",
    "boundary_off_by_one",
    "duplicate_handling",
    "ordering_guarantees",
    "error_handling",
]


@dataclass
class GeneratedTest:
    claim_id: str
    branch_id: str | None
    function_name: str
    code: str


@dataclass
class TestSuite:
    files: list[Path] = field(default_factory=list)
    tests: list[GeneratedTest] = field(default_factory=list)
    edge_categories_targeted: list[str] = field(default_factory=list)
    untraceable_dropped: int = 0


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40] or "claim"


def _extract_code_block(text: str) -> str | None:
    cleaned = text.strip()
    fence = re.search(r"```(?:python)?\s*\n(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    elif cleaned.startswith("```"):
        return None
    if "def test_" not in cleaned:
        return None
    try:
        ast.parse(cleaned)
    except SyntaxError:
        return None
    return cleaned.strip() + "\n"


def _test_function_spans(code: str) -> dict[str, tuple[int, int]]:
    """Map test function name to (start_line, end_line), 1-indexed inclusive."""
    spans = {}
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            start = min(
                [node.lineno] + [d.lineno for d in node.decorator_list]
            )
            spans[node.name] = (start, node.end_lineno)
    return spans


def _apply_cap_and_settings(
    code: str, max_tests: int, max_examples: int, seed: int | None = None,
) -> tuple[str, int]:
    """Keep at most max_tests traceable tests; inject deterministic hypothesis
    settings (R9.3): max_examples plus a logged seed so runs are replayable."""
    lines = code.splitlines(keepends=True)
    spans = _test_function_spans(code)

    traceable = {n: s for n, s in spans.items() if TEST_NAME_RE.match(n)}
    untraceable = len(spans) - len(traceable)

    drop_names = set(spans) - set(sorted(traceable)[:max_tests])
    drop_lines = set()
    for name in drop_names:
        start, end = spans[name]
        drop_lines.update(range(start, end + 1))
    kept_lines = [l for i, l in enumerate(lines, start=1) if i not in drop_lines]
    result = "".join(kept_lines)

    settings_kwargs = f"max_examples={max_examples}"
    if seed is not None:
        settings_kwargs += f", seed={seed}, derandomize=False"
        if "@settings(" in result:
            result = re.sub(
                r"@settings\(([^)]*)\)",
                lambda m: m.group(0) if "seed=" in m.group(1)
                else f"@settings({m.group(1)}, seed={seed})",
                result,
            )
    if "@given(" in result and "@settings(" not in result:
        result = result.replace("@given(", f"@settings({settings_kwargs})\n@given(")
        if re.search(r"^from hypothesis import .*\bgiven\b.*$", result, re.MULTILINE) and \
           not re.search(r"^from hypothesis import .*settings", result, re.MULTILINE):
            first_import = re.search(r"^import|^from", result, re.MULTILINE)
            insert_at = first_import.start() if first_import else 0
            result = result[:insert_at] + "from hypothesis import settings\n" + result[insert_at:]

    return result, untraceable


def _edge_categories_for(claim: Claim) -> list[str]:
    text_lower = claim.text.lower()
    targeted = []
    hints = {
        "empty_and_null_input": ("empty", "null", "none", "missing"),
        "boundary_off_by_one": ("boundary", "range", "at most", "at least", "between"),
        "duplicate_handling": ("duplicate", "unique", "repeated"),
        "ordering_guarantees": ("order", "sorted", "sequence"),
        "error_handling": ("error", "raise", "invalid", "fail"),
    }
    for category, keywords in hints.items():
        if any(k in text_lower for k in keywords):
            targeted.append(category)
    return targeted or ["general_fuzz"]


def synthesize_for_target(
    llm: LLMClient,
    claim: Claim,
    branch: AmbiguityBranch | None,
    signatures: str,
    max_tests_per_claim: int,
    max_examples: int,
    seed: int | None = None,
) -> tuple[str, int]:
    """Generate one capped test file for a claim (or one ambiguity branch).

    Returns (file_code, untraceable_dropped_count).
    """
    payload = claim.to_dict()
    label = branch.branch_id if branch else claim.id
    payload["id"] = label
    if branch is not None:
        payload["text"] = (
            f"Ambiguity branch {branch.branch_id} of claim {claim.id}. "
            f"Interpretation under test: {branch.interpretation}. "
            f"Original claim: {claim.text}"
        )
    user = synthesis_user_prompt(json.dumps(payload), signatures)
    raw = llm.complete(SYNTHESIS_SYSTEM, user).text
    code = _extract_code_block(raw)
    if code is None:
        raise SynthesisError(f"No valid test code generated for {label}.")
    names = [n for n in _test_function_spans(code) if TEST_NAME_RE.match(n)]
    if not names:
        raise SynthesisError(f"Generated tests for {label} carry no traceable claim ID.")
    return _apply_cap_and_settings(code, max_tests_per_claim, max_examples, seed)


def synthesize_suite(
    llm: LLMClient,
    spec: IntentSpec,
    signatures: str,
    out_dir: Path,
    max_tests_per_claim: int,
    max_examples: int,
    seed: int | None = None,
) -> TestSuite:
    """One file per claim; one separate file per ambiguity branch (R2.3)."""
    suite = TestSuite()
    out_dir.mkdir(parents=True, exist_ok=True)
    file_index = 0
    for claim in spec.claims:
        suite.edge_categories_targeted.extend(_edge_categories_for(claim))
        targets: list[tuple[str, AmbiguityBranch | None]] = []
        if claim.ambiguous and claim.branches:
            targets = [(b.branch_id, b) for b in claim.branches]
        else:
            targets = [(claim.id, None)]
        for label, branch in targets:
            try:
                code, dropped = synthesize_for_target(
                    llm, claim, branch, signatures,
                    max_tests_per_claim, max_examples, seed,
                )
            except SynthesisError as e:
                print(f"[ide] synthesis skipped for {label}: {e}")
                continue
            suite.untraceable_dropped += dropped
            file_index += 1
            safe_label = label.replace(".", "_")
            path = out_dir / f"test_gen_{file_index:03d}_{safe_label}.py"
            path.write_text(code, encoding="utf-8")
            suite.files.append(path)
            for func_name in _test_function_spans(code):
                if TEST_NAME_RE.match(func_name):
                    suite.tests.append(
                        GeneratedTest(
                            claim_id=claim.id,
                            branch_id=branch.branch_id if branch else None,
                            function_name=func_name,
                            code=code,
                        )
                    )
    seen = set()
    deduped = []
    for c in suite.edge_categories_targeted:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    suite.edge_categories_targeted = deduped
    return suite
