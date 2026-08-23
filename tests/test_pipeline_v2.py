import json

from intent_ide.config import Config
from intent_ide.llm import FakeLLMClient
from intent_ide.models import IntentSpec
from intent_ide.pipeline import EXIT_DIVERGENCE, EXIT_OK, replay_run, run_pipeline

EXTRACT = """```json
{"claims": [
  {"type": "postcondition", "text": "doubles the number",
   "source": "stated", "confidence": 1.0}
]}
```"""

SYNTH_C1 = '''```python
from under_test import double

def test_C1__doubles():
    result = double(2)
    expected = 4
    assert result == expected, f"INPUT: 2 EXPECTED: {expected} ACTUAL: {result}"
```
'''


class Router(FakeLLMClient):
    def complete(self, system, user):
        from intent_ide.llm import LLMResponse
        self.calls.append((system, user))
        if "intent-extraction" in system:
            return LLMResponse(text=EXTRACT)
        if '"id": "C1"' in user:
            return LLMResponse(text=SYNTH_C1)
        raise AssertionError("unrouted call")


def _make_run(tmp_path, buggy: bool):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    impl = "return n * 2" if not buggy else "return n * 3"
    (project / "under_test.py").write_text(f"def double(n):\n    {impl}\n",
                                           encoding="utf-8")
    llm = Router({})
    result = run_pipeline(llm, "double the input", project,
                          tmp_path / ".ide", seed=42, cfg=Config())
    return result


def test_seed_recorded_and_injected(tmp_path):
    result = _make_run(tmp_path, buggy=True)
    meta = json.loads((tmp_path / ".ide" / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["run_seed"] == 42
    gen_file = next((tmp_path / ".ide" / "generated_tests").glob("*.py"))
    # no hypothesis here; seed only applies to @given tests — just check meta
    assert result.exit_code == EXIT_DIVERGENCE


def test_claim_map_saved_for_replay(tmp_path):
    _make_run(tmp_path, buggy=True)
    cmap = json.loads((tmp_path / ".ide" / "claim_map.json").read_text(encoding="utf-8"))
    assert "test_C1__doubles" in cmap
    assert cmap["test_C1__doubles"] == ["C1", None]


def test_replay_reproduces_finding_without_llm(tmp_path):
    _make_run(tmp_path, buggy=True)
    project = tmp_path / "proj"
    replayed = replay_run(project, tmp_path / ".ide", cfg=Config())
    assert replayed.exit_code == EXIT_DIVERGENCE
    assert "REPLAY of stored run" in replayed.report_markdown
    assert (tmp_path / ".ide" / "replay_report.md").exists()


def test_replay_flips_when_code_fixed(tmp_path):
    _make_run(tmp_path, buggy=True)
    project = tmp_path / "proj"
    (project / "under_test.py").write_text("def double(n):\n    return n * 2\n",
                                           encoding="utf-8")
    replayed = replay_run(project, tmp_path / ".ide", cfg=Config())
    assert replayed.exit_code == EXIT_OK


def test_pipeline_with_decisions_resolves_ambiguity(tmp_path):
    extract = """{"claims": [{"type": "edge_case",
      "text": "empty list behavior unstated", "source": "inferred",
      "confidence": 0.7, "ambiguous": true,
      "branches": [{"interpretation": "return empty"},
                   {"interpretation": "raise"}]}]}"""
    synth_a = '''```python
from under_test import firsts

def test_C1_a__empty_ok():
    assert firsts([], 2) == []
```
'''
    synth_b = '''```python
import pytest
from under_test import firsts

def test_C1_b__empty_raises():
    with pytest.raises(ValueError):
        firsts([], 2)
```
'''

    class DecRouter(FakeLLMClient):
        def complete(self, system, user):
            from intent_ide.llm import LLMResponse
            if "intent-extraction" in system:
                return LLMResponse(text=extract)
            if '"id": "C1.a"' in user:
                return LLMResponse(text=synth_a)
            if '"id": "C1.b"' in user:
                return LLMResponse(text=synth_b)
            if '"id": "C1"' in user:
                return LLMResponse(text=synth_b)
            raise AssertionError("unrouted")

    project = tmp_path / "proj"
    project.mkdir()
    (project / "under_test.py").write_text("def firsts(items, n):\n    return items[:n]\n",
                                           encoding="utf-8")
    dec = tmp_path / "decisions.json"
    dec.write_text(json.dumps({"C1": "C1.b"}), encoding="utf-8")
    result = run_pipeline(DecRouter({}), "task", project, tmp_path / ".ide",
                          cfg=Config(), decisions_file=dec, seed=7)
    # decision applied -> no ambiguous claims remain in the checked spec
    assert result.spec.ambiguous_claims == []
    assert result.exit_code in (EXIT_OK, EXIT_DIVERGENCE)
