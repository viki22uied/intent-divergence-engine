"""Intent description sources (R8.2): one source per run.

v1 supports: inline text / file, GitHub PR description, or a ticket ID
referenced from the PR. Multi-source merging is intentionally out of scope.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
PR_REF_RE = re.compile(r"^(?:https?://github\.com/)?([\w.-]+/[\w.-]+)(?:#|/pull/)(\d+)/?$")


class SourceError(RuntimeError):
    pass


def _gh_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def _github_get(url: str) -> dict:
    token = _gh_token()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "intent-divergence-engine"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SourceError(f"GitHub API HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"GitHub request failed: {e}") from e


def parse_pr_ref(ref: str) -> tuple[str, int]:
    m = PR_REF_RE.match(ref.strip())
    if not m:
        raise SourceError(
            f"Invalid --github-pr ref: {ref!r}. Use 'owner/repo#123' or a PR URL.")
    return m.group(1), int(m.group(2))


def fetch_pr_task(ref: str) -> str:
    """Fetch the PR description as the task description. If the PR body links a
    ticket keyword (Fixes/Closes/Refs #n), the referenced issue body is appended
    as secondary context — still one source *type* (GitHub), per R8.2."""
    repo, number = parse_pr_ref(ref)
    pr = _github_get(f"{GITHUB_API}/repos/{repo}/pulls/{number}")
    body = pr.get("body") or ""
    parts = [f"PR #{number}: {pr.get('title', '')}".strip(), "", body]
    linked = re.findall(
        r"(?:fixes|closes|resolves|refs?|addresses)\s+#(\d+)",
        body, re.IGNORECASE)
    for issue_num in linked[:1]:  # v1: at most one linked ticket
        try:
            issue = _github_get(f"{GITHUB_API}/repos/{repo}/issues/{issue_num}")
            parts += ["", "---", f"Linked issue #{issue_num}: {issue.get('title', '')}",
                      "", issue.get("body") or ""]
        except SourceError:
            parts += ["", f"(linked issue #{issue_num} could not be fetched)"]
    text = "\n".join(parts).strip()
    if not body.strip():
        raise SourceError(
            f"PR {repo}#{number} has no usable description body; "
            "a title alone is not enough intent.")
    return text


def resolve_task_description(args) -> str:
    """Priority: explicit text/file > GitHub PR ref. Exactly one wins."""
    if getattr(args, "task_file", None):
        import pathlib
        return pathlib.Path(args.task_file).read_text(encoding="utf-8")
    if getattr(args, "task_text", None):
        return args.task_text
    gh_pr = getattr(args, "github_pr", None)
    if gh_pr:
        return fetch_pr_task(gh_pr)
    import sys
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    raise SourceError("Provide --task-file, --task-text, --github-pr, or pipe task text via stdin.")
