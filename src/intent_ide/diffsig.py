"""Extract changed Python function signatures from a unified diff.

Feeds Stage 2 with the signatures actually touched by the change, so test
synthesis targets the real API surface of the diff instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DEF_RE = re.compile(r"^(\s*)def\s+(?:async\s+)?(\w+)\s*\(([^)]*)", re.MULTILINE)
FILE_RE = re.compile(r"^\+\+\+\s+b/(.+?)\s*$", re.MULTILINE)
HUNK_RE = re.compile(r"^@@", re.MULTILINE)


@dataclass
class ChangedFunction:
    file: str
    name: str
    signature: str


def extract_signatures_from_diff(diff_text: str) -> list[ChangedFunction]:
    """Return top-level (non-indented) function defs among ADDED lines."""
    results: list[ChangedFunction] = []
    current_file = ""
    added_lines: list[str] = []

    def flush():
        if current_file and added_lines:
            results.extend(_scan(current_file, added_lines))

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            flush()
            current_file = line[6:].strip()
            added_lines = []
        elif line.startswith("@@"):
            if added_lines:
                results.extend(_scan(current_file, added_lines))
            added_lines = []
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
        elif line.startswith(("diff ", "index ", "--- ", "new file", "deleted file")):
            continue
    flush()
    deduped: list[ChangedFunction] = []
    seen = set()
    for fn in results:
        key = (fn.file, fn.signature)
        if key not in seen and not fn.name.startswith("_"):
            seen.add(key)
            deduped.append(fn)
    return deduped


def _scan(file: str, added_lines: list[str]) -> list[ChangedFunction]:
    found = []
    text = "\n".join(added_lines)
    for m in DEF_RE.finditer(text):
        indent, name, args = m.group(1), m.group(2), m.group(3)
        if indent:  # only module-level or class-level public defs
            continue
        found.append(ChangedFunction(
            file=file,
            name=name,
            signature=f"def {name}({args.strip()}" + (")" if ")" in args else "..."),
        ))
    return found


def signatures_prompt_block(functions: list[ChangedFunction]) -> str:
    if not functions:
        return ""
    lines = ["Functions changed by this diff:"]
    for fn in functions:
        lines.append(f"- {fn.file}: {fn.signature}")
    return "\n".join(lines)
