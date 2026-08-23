import ast

from intent_ide.synthesize import _apply_cap_and_settings

CODE = """import pytest
from hypothesis import given
from hypothesis import strategies as st

@given(st.integers())
def test_C5__invariant_holds(x):
    assert x >= 0
"""


def test_seed_injected_into_settings():
    out, dropped = _apply_cap_and_settings(CODE, 3, 50, seed=1234)
    assert "@settings(max_examples=50, seed=1234" in out
    assert dropped == 0
    ast.parse(out)


def test_existing_settings_get_seed():
    code = CODE.replace("@given(", "@settings(max_examples=10)\n@given(", 1)
    out, _ = _apply_cap_and_settings(code, 3, 50, seed=77)
    assert "seed=77" in out
    assert "max_examples=10" in out
    ast.parse(out)


def test_no_seed_when_none():
    out, _ = _apply_cap_and_settings(CODE, 3, 50, seed=None)
    assert "@settings(max_examples=50)" in out


def test_seed_not_duplicated():
    out, _ = _apply_cap_and_settings(CODE, 3, 50, seed=9)
    out2, _ = _apply_cap_and_settings(out, 3, 50, seed=9)
    assert out2.count("seed=") == out.count("seed=")


def test_untraceable_tests_dropped():
    code = CODE + """
def helper_not_a_test(x):
    return x
"""
    out, dropped = _apply_cap_and_settings(code + "\n\ndef test_badname(x):\n    assert x\n", 3, 10, seed=1)
    assert "test_badname" not in out
