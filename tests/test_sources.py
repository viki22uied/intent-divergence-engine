import json

import pytest

from intent_ide import sources
from intent_ide.sources import SourceError, fetch_pr_task, parse_pr_ref


def test_parse_pr_ref_forms():
    assert parse_pr_ref("owner/repo#12") == ("owner/repo", 12)
    assert parse_pr_ref("https://github.com/owner/repo/pull/99") == ("owner/repo", 99)
    with pytest.raises(SourceError):
        parse_pr_ref("not-a-ref")


def test_fetch_pr_task(monkeypatch):
    def fake_get(url):
        if url.endswith("/pulls/7"):
            return {"title": "Add cart total", "body": "Fixes #8\n\nSum only positive prices."}
        if url.endswith("/issues/8"):
            return {"title": "Cart bug", "body": "Total must ignore refunds."}
        raise AssertionError(url)

    monkeypatch.setattr(sources, "_github_get", fake_get)
    task = fetch_pr_task("owner/repo#7")
    assert "Add cart total" in task
    assert "Linked issue #8" in task
    assert "Total must ignore refunds." in task


def test_empty_body_rejected(monkeypatch):
    monkeypatch.setattr(sources, "_github_get", lambda url: {"title": "t", "body": ""})
    with pytest.raises(SourceError):
        fetch_pr_task("owner/repo#1")
