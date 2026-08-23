"""Stage 3 - Execution (R3.1-R3.4)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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


def _watch_peak_memory(proc: subprocess.Popen, deadline: float) -> float | None:
    """R3.4: sample child peak RSS while the suite runs, bounded by deadline.
    Optional — requires psutil; silently skipped when unavailable."""
    try:
        import psutil
    except ImportError:
        return None
    peak_bytes = 0
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
            except psutil.NoSuchProcess:
                break
            time.sleep(0.2)
    except psutil.Error:
        return None
    return round(peak_bytes / (1024 * 1024), 1) if peak_bytes else None


def run_suite(
    project_dir: Path,
    generated_tests_dir: Path,
    claim_map: dict[str, tuple[str, str | None]],
    suite_timeout_seconds: int,
    test_timeout_seconds: int,
) -> SuiteRun:
    """Execute generated tests against the code under test in a sandboxed
    subprocess (R3.1), with hard timeouts (R3.2)."""
    run = SuiteRun()
    junit_path = generated_tests_dir / "junit_report.xml"
    node_ids = [str(p.resolve()) for p in sorted(generated_tests_dir.glob("test_gen_*.py"))]
    if not node_ids:
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
    try:
        popen = subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            env=build_sandbox_env({"PYTHONPATH": os.pathsep.join(
                filter(None, [str(project_dir), str(generated_tests_dir),
                              os.environ.get("IDE_EXTRA_PYTHONPATH", "")])
            )}),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        peak_mb = _watch_peak_memory(popen, deadline)
        remaining = max(0.1, deadline - time.monotonic())
        try:
            stdout, _stderr = popen.communicate(timeout=remaining)
            run.stdout_tail = "\n".join((stdout or "").splitlines()[-40:])
        except subprocess.TimeoutExpired:
            run.timed_out = True
            popen.kill()
            popen.communicate()
    finally:
        run.duration_seconds = time.monotonic() - started  # R3.4
    if peak_mb is not None:
        run.peak_memory_mb = peak_mb

    if junit_path.exists():
        run.results = _parse_junit(junit_path, claim_map)
    else:
        # whole-suite timeout: record each test as timeout so nothing is silently dropped
        for fname in node_ids:
            run.results.append(TestResult(
                test_id=fname, function_name=fname, outcome="timeout",
                message=f"suite exceeded {suite_timeout_seconds}s hard timeout",
            ))
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
