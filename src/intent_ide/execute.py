"""Stage 3 - Execution (R3.1-R3.4)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .safety import validate_generated_code

ENV_ALLOWLIST = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT",
                 "TEMP", "TMP", "WINDIR", "HOME", "USERPROFILE", "PYTHONPATH"}
SECRET_PATTERN = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AWS_|AZURE_|GCP_", re.IGNORECASE)


class SandboxBreach(Exception):
    pass


def build_sandbox_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k in ENV_ALLOWLIST and not SECRET_PATTERN.search(k)}
    if extra:
        for k, v in extra.items():
            if SECRET_PATTERN.search(k):
                raise SandboxBreach(f"Refusing to pass secret-looking var: {k}")
            env[k] = v
    return env


@dataclass
class TestResult:
    __test__ = False
    test_id: str          # pytest node id
    function_name: str
    outcome: str          # passed | failed | error | timeout
    message: str = ""
    claim_id: str | None = None
    branch_id: str | None = None
    failing_input: str | None = None
    expected: str | None = None
    actual: str | None = None


@dataclass
class SuiteRun:
    __test__ = False
    results: list[TestResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    peak_memory_mb: float | None = None  # R3.4: logged, not enforced
    timed_out: bool = False
    stdout_tail: str = ""


INPUT_RE = re.compile(r"INPUT:\s*(.+?)(?:\s*EXPECTED:|$)", re.IGNORECASE | re.DOTALL)
EXPECTED_RE = re.compile(r"EXPECTED:\s*(.+?)(?:\s*ACTUAL:|$)", re.IGNORECASE | re.DOTALL)
ACTUAL_RE = re.compile(r"ACTUAL:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_failure_detail(message: str) -> tuple[str | None, str | None, str | None]:
    f_input = INPUT_RE.search(message)
    f_exp = EXPECTED_RE.search(message)
    f_act = ACTUAL_RE.search(message)
    return (
        f_input.group(1).strip() if f_input else None,
        f_exp.group(1).strip() if f_exp else None,
        f_act.group(1).strip() if f_act else None,
    )


def _watch_peak_memory(
    proc: subprocess.Popen, deadline: float, memory_limit_mb: int | None = None
) -> tuple[float | None, bool]:
    """R3.4: sample child peak RSS while the suite runs, bounded by deadline.
    Returns (peak_mb, killed_for_memory). Optional — requires psutil."""
    try:
        import psutil
    except ImportError:
        return None, False
    peak_bytes = 0
    killed = False
    limit_bytes = (memory_limit_mb * 1024 * 1024) if memory_limit_mb else None
    try:
        proc_ps = psutil.Process(proc.pid)
        while proc.poll() is None and time.monotonic() < deadline:
            try:
                mem = proc_ps.memory_info().rss
                for child in proc_ps.children(recursive=True):
                    try:
                        mem += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak_bytes = max(peak_bytes, mem)
                if limit_bytes and mem > limit_bytes and not killed:
                    # enforce memory ceiling — not just log (Finding 4)
                    try:
                        proc.kill()
                        killed = True
                        break
                    except OSError:
                        pass
            except psutil.NoSuchProcess:
                break
            time.sleep(0.2)
    except psutil.Error:
        return None, False
    peak_mb = round(peak_bytes / (1024 * 1024), 1) if peak_bytes else None
    return peak_mb, killed


def _is_sandboxed() -> bool:
    return (
        os.environ.get("IDE_SANDBOXED") == "1"
        or os.environ.get("IDE_ALLOW_UNSANDBOXED") == "1"
        or Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
    )


def _sandbox_warning() -> str | None:
    if _is_sandboxed():
        return None
    return (
        "Execution is NOT container-isolated. Generated LLM code will run with "
        "full host privileges. Set IDE_SANDBOXED=1 when running inside a "
        "--network none container, or IDE_ALLOW_UNSANDBOXED=1 to acknowledge the risk. "
        "See README Security section."
    )


def _clear_pycache(project_dir: Path) -> None:
    # Windows mtime granularity is 1s; stale .pyc can survive a quick
    # source overwrite (as in replay tests) and cause the old bytecode to
    # be reused. Clear it before each pytest invocation.
    for p in project_dir.rglob("__pycache__"):
        try:
            shutil.rmtree(p)
        except OSError:
            pass
    for p in project_dir.rglob("*.pyc"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in project_dir.rglob("*.pyo"):
        try:
            p.unlink()
        except OSError:
            pass


def run_suite(
    project_dir: Path,
    generated_tests_dir: Path,
    claim_map: dict[str, tuple[str, str | None]],
    suite_timeout_seconds: int,
    test_timeout_seconds: int,
    memory_limit_mb: int | None = None,
) -> SuiteRun:
    """Execute generated tests against the code under test in a sandboxed
    subprocess (R3.1), with hard timeouts (R3.2).

    Defense-in-depth: each generated file is re-validated via the AST safety
    gate before execution. Blocked files are reported as errors, not silently
    dropped, and never executed (Finding 1).
    """
    _clear_pycache(project_dir)
    run = SuiteRun()
    junit_path = generated_tests_dir / "junit_report.xml"
    candidate_files = sorted(generated_tests_dir.glob("test_gen_*.py"))
    node_ids: list[str] = []
    blocked: list[TestResult] = []
    for p in candidate_files:
        try:
            code = p.read_text(encoding="utf-8")
        except OSError:
            continue
        safe, reason = validate_generated_code(code)
        if not safe:
            blocked.append(TestResult(
                test_id=str(p.resolve()),
                function_name=p.stem,
                outcome="error",
                message=f"blocked by safety gate: {reason}",
            ))
        else:
            node_ids.append(str(p.resolve()))
    # preserve blocked results even if nothing else runs
    if not node_ids:
        run.results = blocked
        warn = _sandbox_warning()
        if warn and blocked:
            # still surface the sandbox warning alongside the block
            run.stdout_tail = warn
        elif warn:
            run.stdout_tail = warn
        return run

    cmd = [
        sys.executable, "-m", "pytest",
        *node_ids,
        "-p", "no:cacheprovider",
        "--junitxml", str(junit_path),
        f"--timeout={test_timeout_seconds}",
        "--timeout-method=thread",
        "-v",
        "--no-header",
    ]
    started = time.monotonic()
    deadline = started + suite_timeout_seconds
    popen = None
    killed_for_memory = False
    try:
        popen = subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            env=build_sandbox_env({
                "PYTHONPATH": os.pathsep.join(
                    filter(None, [str(project_dir), str(generated_tests_dir),
                                  os.environ.get("IDE_EXTRA_PYTHONPATH", "")])
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            }),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        peak_mb, killed_for_memory = _watch_peak_memory(popen, deadline, memory_limit_mb)
        remaining = max(0.1, deadline - time.monotonic())
        try:
            stdout, _stderr = popen.communicate(timeout=remaining)
            run.stdout_tail = "\n".join((stdout or "").splitlines()[-40:])
        except subprocess.TimeoutExpired:
            run.timed_out = True
            popen.kill()
            popen.communicate()
        if killed_for_memory:
            run.timed_out = True
            run.stdout_tail += f"\n[killed: exceeded memory limit {memory_limit_mb} MB]"
    finally:
        run.duration_seconds = time.monotonic() - started  # R3.4
    if peak_mb is not None:
        run.peak_memory_mb = peak_mb
    if killed_for_memory:
        # record as timeout-like so it surfaces in the report
        run.results = blocked + [
            TestResult(
                test_id=fname,
                function_name=fname,
                outcome="timeout",
                message=f"killed: exceeded memory limit {memory_limit_mb} MB",
            )
            for fname in node_ids
        ]
        return run

    if junit_path.exists():
        parsed = _parse_junit(junit_path, claim_map)
        run.results = blocked + parsed
    else:
        # whole-suite timeout: record each test as timeout so nothing is silently dropped
        for fname in node_ids:
            run.results.append(TestResult(
                test_id=fname, function_name=fname, outcome="timeout",
                message=f"suite exceeded {suite_timeout_seconds}s hard timeout",
            ))
        run.results = blocked + run.results
    warn = _sandbox_warning()
    if warn:
        # surface in stdout_tail so pipeline/report can surface it
        run.stdout_tail = (run.stdout_tail + "\n" + warn).strip()
    return run


def _parse_junit(path: Path, claim_map: dict[str, tuple[str, str | None]]) -> list[TestResult]:
    tree = ET.parse(path)
    results = []
    for case in tree.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        module = Path(classname.replace(".", "/")).name if classname else ""
        full_name = name if not module else name
        lookup_name = name
        if lookup_name.startswith("test_") and "(" in lookup_name:
            lookup_name = lookup_name.split("(")[0]
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            outcome = "failed"
            detail = failure.get("message", "") or (failure.text or "")
        elif error is not None:
            outcome = "error"
            detail = error.get("message", "") or (error.text or "")
        elif skipped is not None:
            outcome = "error"
            detail = "skipped: " + (skipped.get("message", "") or "")
        else:
            outcome = "passed"
            detail = ""
        claim_id, branch_id = claim_map.get(lookup_name, (None, None))
        f_input, f_expected, f_actual = _parse_failure_detail(detail) if outcome == "failed" else (None, None, None)
        results.append(TestResult(
            test_id=f"{module}::{name}",
            function_name=lookup_name,
            outcome=outcome,
            message=detail.strip()[:2000],
            claim_id=claim_id,
            branch_id=branch_id,
            failing_input=f_input,
            expected=f_expected,
            actual=f_actual,
        ))
    return results
