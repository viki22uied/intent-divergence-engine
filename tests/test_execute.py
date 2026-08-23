import sys
import textwrap

from intent_ide.execute import build_sandbox_env, run_suite


UNDER_TEST = "def sum_positives(nums):\n    return sum(nums)\n"

GENERATED = textwrap.dedent("""\
    import pytest
    from under_test import sum_positives

    def test_C1__sums_only_positives():
        nums = [-1, 2, 3]
        result = sum_positives(nums)
        expected = 5
        assert result == expected, (
            f"INPUT: {nums} EXPECTED: {expected} ACTUAL: {result}"
        )
""")


def test_end_to_end_execution_detects_divergence(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "under_test.py").write_text(UNDER_TEST, encoding="utf-8")
    gen_dir = project / ".ide" / "gen"
    gen_dir.mkdir(parents=True)
    (gen_dir / "test_gen_001_C1.py").write_text(GENERATED, encoding="utf-8")

    run = run_suite(
        project_dir=project,
        generated_tests_dir=gen_dir,
        claim_map={"test_C1__sums_only_positives": ("C1", None)},
        suite_timeout_seconds=120,
        test_timeout_seconds=20,
    )
    assert len(run.results) == 1
    r = run.results[0]
    assert r.outcome == "failed"
    assert r.claim_id == "C1"
    assert "-1" in (r.failing_input or "")
    assert "5" in (r.expected or "")


def test_passing_case(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "under_test.py").write_text(
        "def sum_positives(nums):\n    return sum(n for n in nums if n > 0)\n",
        encoding="utf-8",
    )
    gen_dir = project / ".ide" / "gen"
    gen_dir.mkdir(parents=True)
    (gen_dir / "test_gen_001_C1.py").write_text(GENERATED, encoding="utf-8")

    run = run_suite(project, gen_dir,
                    {"test_C1__sums_only_positives": ("C1", None)}, 60, 15)
    assert run.results[0].outcome == "passed"


def test_sandbox_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("MY_API_TOKEN", "y")
    monkeypatch.setenv("PATH", "kept")
    env = build_sandbox_env({"IDE_SAFE": "1"})
    assert env.get("PATH") == "kept"
    assert "MY_API_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    try:
        build_sandbox_env({"SOME_PASSWORD": "z"})
        raised = False
    except Exception:
        raised = True
    assert raised


def test_suite_timeout_records_timeouts(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    gen_dir = project / ".ide" / "gen"
    gen_dir.mkdir(parents=True)
    (gen_dir / "test_gen_001_C1.py").write_text(
        "def test_C1__hang():\n    import time; time.sleep(30)\n", encoding="utf-8")
    run = run_suite(project, gen_dir,
                    {"test_C1__hang": ("C1", None)},
                    suite_timeout_seconds=5, test_timeout_seconds=2)
    assert run.results and all(r.outcome in ("timeout", "error") for r in run.results)
