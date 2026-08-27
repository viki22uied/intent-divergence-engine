import pytest

from intent_ide.safety import validate_generated_code


def assert_blocked(code: str):
    safe, reason = validate_generated_code(code)
    assert not safe, f"expected blocked but was safe: {reason}"
    assert "blocked" in reason.lower()


def assert_safe(code: str):
    safe, reason = validate_generated_code(code)
    assert safe, f"expected safe but was blocked: {reason}"


# --- directly blocked imports (original gate) ---

def test_blocks_os_import():
    assert_blocked("import os\ndef test_C1__x():\n    assert True\n")


def test_blocks_subprocess_import():
    assert_blocked("import subprocess\ndef test_C1__x():\n    subprocess.run(['id'])\n")


def test_blocks_socket_import():
    assert_blocked("import socket\ndef test_C1__x():\n    s=socket.socket()\n")


def test_blocks_open_call():
    assert_blocked("def test_C1__x():\n    open('/etc/passwd').read()\n")


def test_blocks_eval_call():
    assert_blocked("def test_C1__x():\n    eval('1+1')\n")


def test_blocks_dunder_import():
    assert_blocked("def test_C1__x():\n    __import__('os').system('id')\n")


# --- alias bypass (re-audit) ---

def test_blocks_alias_open():
    assert_blocked("def test_C1__x():\n    f = open\n    f('/etc/passwd').read()\n")


def test_blocks_alias_eval():
    assert_blocked("def test_C1__x():\n    my_eval = eval\n    my_eval('1+1')\n")


# --- extended denied imports (re-audit) ---

@pytest.mark.parametrize("mod", ["glob", "sqlite3", "xmlrpc", "ftplib", "smtplib", "telnetlib", "poplib", "imaplib", "ssl", "tarfile", "zipfile", "pickle"])
def test_blocks_extended_imports(mod):
    assert_blocked(f"import {mod}\ndef test_C1__x():\n    assert True\n")


def test_blocks_xmlrpc_client_from():
    assert_blocked("from xmlrpc import client\ndef test_C1__x():\n    pass\n")


def test_blocks_glob_from():
    assert_blocked("from glob import glob\ndef test_C1__x():\n    glob('/etc/*')\n")


# --- allowed cases (must not false-positive) ---

def test_allows_pytest_and_hypothesis():
    assert_safe("""
import pytest
from hypothesis import given, strategies as st
from under_test import foo
def test_C1__ok():
    assert foo(1) == 1
""")


def test_allows_typing_math():
    assert_safe("""
import typing
import math
from under_test import bar
def test_C1__ok():
    assert math.isclose(bar(1), 1.0)
""")


def test_blocks_top_level_call():
    assert_blocked("import os\nos.system('id')\ndef test_C1__x():\n    assert True\n")


def test_globals_bypass_is_known_ceiling():
    """Documents the denylist ceiling: globals()/getattr indirection is NOT
    blocked — this is expected. The gate is defense-in-depth only; real
    isolation must come from a container (see safety.py docstring and README
    Security). If this test starts failing (i.e. the payload becomes blocked),
    update the docstring to reflect the new boundary."""
    payload = """
def test_C1__x():
    g = globals()
    b = g["__builtins__"]
    imp = b["__import__"] if isinstance(b, dict) else getattr(b, "__import__")
    os_mod = imp("os")
    os_mod.system("id")
    assert True
"""
    safe, reason = validate_generated_code(payload)
    assert safe, f"expected safe (known bypass) but was blocked: {reason}"
