import textwrap
from pathlib import Path

import pytest

from intent_ide.execute import run_suite


def test_memory_limit_kills_and_reports(tmp_path, monkeypatch):
    """_watch_peak_memory returning killed=True must surface as timeout with limit message."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "under_test.py").write_text("def foo(x): return x\n")

    gen = proj / ".ide" / "gen"
    gen.mkdir(parents=True)
    (gen / "test_gen_001_C1.py").write_text(textwrap.dedent("""
        from under_test import foo
        def test_C1__ok():
            assert foo(1) == 1
    """))

    # force the memory watcher to claim the limit was exceeded
    monkeypatch.setattr("intent_ide.execute._watch_peak_memory", lambda proc, deadline, memory_limit_mb=None: (2000.0, True))
    monkeypatch.setenv("IDE_ALLOW_UNSANDBOXED", "1")

    run = run_suite(proj, gen, {"test_C1__ok": ("C1", None)}, suite_timeout_seconds=10, test_timeout_seconds=5, memory_limit_mb=10)

    assert any("exceeded memory limit" in r.message for r in run.results)
    assert any(r.outcome == "timeout" for r in run.results)


def test_memory_limit_not_triggered_normally(tmp_path, monkeypatch):
    proj = tmp_path / "proj2"
    proj.mkdir()
    (proj / "under_test.py").write_text("def foo(x): return x\n")
    gen = proj / ".ide" / "gen"
    gen.mkdir(parents=True)
    (gen / "test_gen_001_C1.py").write_text(textwrap.dedent("""
        from under_test import foo
        def test_C1__ok():
            assert foo(1) == 1
    """))
    monkeypatch.setenv("IDE_ALLOW_UNSANDBOXED", "1")
    # real psutil path (or no psutil) — should not kill for tiny test
    run = run_suite(proj, gen, {"test_C1__ok": ("C1", None)}, suite_timeout_seconds=10, test_timeout_seconds=5, memory_limit_mb=1024)
    assert any(r.outcome == "passed" for r in run.results)
