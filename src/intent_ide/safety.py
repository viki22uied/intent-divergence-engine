"""AST safety gate for LLM-generated test code (Finding 1 defense-in-depth).

This is NOT a sandbox — it is a denylist check that rejects obviously
dangerous generated files before they are written or executed. The real
containment must come from container/VM isolation (see execute.py docstring);
this gate makes accidental or naive prompt-injection payloads visible as
'blocked: disallowed import' rather than silently executed.
"""
from __future__ import annotations

import ast

DENIED_IMPORT_ROOTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "importlib",
    "urllib",
    "http",
    "requests",
    "pathlib",
    "signal",
    "multiprocessing",
    "threading",
    "asyncio",
    "inspect",
    "pkgutil",
    "pydoc",
    "builtins",
    "importlib_metadata",
    # cloud metadata / credential-adjacent
    "boto3",
    "botocore",
    "google",
    "azure",
}

DENIED_CALL_NAMES = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
DENIED_ATTRS = {"__subclasses__", "__bases__", "__mro__", "__code__", "__globals__"}

# We allow only a small set of top-level statement types in generated test
# modules — test files should be imports + function defs, not arbitrary scripts.
ALLOWED_TOP_LEVEL = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign)


def validate_generated_code(code: str) -> tuple[bool, str]:
    """Return (is_safe, reason_if_blocked). Safe means no denied pattern found."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"syntax error: {e}"

    # 1. top-level shape: reject bare Expr(Call) like `os.system(...)` at import time
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return False, "blocked: top-level function call (module code executes at import time)"

    # 2. walk for denied imports / calls / attrs / names
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"__builtins__", "__builtin__", "__spec__", "__loader__"}:
            return False, f"blocked: disallowed name '{node.id}'"
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DENIED_IMPORT_ROOTS:
                    return False, f"blocked: disallowed import '{alias.name}'"
                # also catch dunder tricks like `import __builtins__`
                if root.startswith("__"):
                    return False, f"blocked: suspicious import '{alias.name}'"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in DENIED_IMPORT_ROOTS:
                    return False, f"blocked: disallowed import from '{node.module}'"
                if root.startswith("__"):
                    return False, f"blocked: suspicious import from '{node.module}'"
            for alias in node.names:
                if alias.name in DENIED_CALL_NAMES or alias.name in DENIED_ATTRS:
                    return False, f"blocked: disallowed import name '{alias.name}'"
        elif isinstance(node, ast.Call):
            # direct calls like eval(...), open(...)
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in DENIED_CALL_NAMES:
                return False, f"blocked: disallowed call '{name}(...)'"
            if name in DENIED_ATTRS:
                return False, f"blocked: disallowed attribute access '{name}'"
            # detect __import__('os') pattern explicitly
            if isinstance(func, ast.Name) and func.id == "__import__":
                return False, "blocked: __import__ is not allowed"
        elif isinstance(node, ast.Attribute):
            if node.attr in DENIED_ATTRS:
                return False, f"blocked: disallowed attribute '{node.attr}'"
            if node.attr == "__import__":
                return False, "blocked: __import__ attribute access"

    return True, ""
