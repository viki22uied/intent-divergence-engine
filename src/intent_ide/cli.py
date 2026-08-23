"""IDE CLI (R8.3 local use; sources R8.2; replay R9.3; post-comment R4.3)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import __version__
from .config import Config
from .decisions import write_decisions_template, DecisionsError
from .diffsig import extract_signatures_from_diff, signatures_prompt_block
from .llm import LLMError, make_client
from .pipeline import (
    EXIT_AMBIGUITY,
    EXIT_DIVERGENCE,
    EXIT_OK,
    EXIT_SYSTEM_FAILURE,
    replay_run,
    run_pipeline,
)
from .sources import SourceError, resolve_task_description

EXIT_LABELS = {
    EXIT_OK: "no divergence found",
    EXIT_DIVERGENCE: "confirmed divergences found",
    EXIT_AMBIGUITY: "ambiguous intent needs a human decision",
    EXIT_SYSTEM_FAILURE: "engine system failure (not a code finding)",
}

COMMENT_MARKER = "<!-- intent-divergence-engine -->"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ide",
        description="Intent Divergence Engine: check code behavior against stated intent.",
    )
    p.add_argument("--version", action="version", version=f"ide {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the full pipeline")
    run.add_argument("--task-file", help="path to task description (ticket/prompt text)")
    run.add_argument("--task-text", help="task description inline")
    run.add_argument("--github-pr", help="PR as intent source: 'owner/repo#123' or URL (R8.2)")
    run.add_argument("--project-dir", default=".", help="code under test root (default: cwd)")
    run.add_argument("--signatures-file", help="file with function/module signatures touched by the change")
    run.add_argument("--diff-file", help="unified git diff of the change; changed signatures are auto-extracted")
    run.add_argument("--decisions", dest="decisions_file",
                     help="JSON file resolving ambiguity branches ({claim_id: branch_id})")
    run.add_argument("--seed", type=int, default=None,
                     help="pin the hypothesis seed for reproducible runs (R9.3)")
    run.add_argument("--out-dir", default=".ide/runs/latest", help="artifact output directory")
    run.add_argument("--max-tests-per-claim", type=int, dest="max_tests_per_claim",
                     help="cost cap: max generated tests per claim (R9.5)")
    run.add_argument("--suite-timeout-seconds", type=int, default=None)
    run.add_argument("--test-timeout-seconds", type=int, default=None)

    replay = sub.add_parser("replay", help="re-execute a stored run's tests deterministically (R9.3)")
    replay.add_argument("run_dir", help="artifact dir from a previous ide run")
    replay.add_argument("--project-dir", default=".", help="code under test root")

    post = sub.add_parser("post-comment", help="post report.md to a GitHub PR (R4.3)")
    post.add_argument("--repo", required=True, help="'owner/repo'")
    post.add_argument("--pr", type=int, required=True, help="PR number")
    post.add_argument("--report", default=".ide/runs/latest/report.md")
    post.add_argument("--update", action="store_true", default=True,
                      help="update existing IDE comment instead of adding a new one")

    tmpl = sub.add_parser("decisions-template",
                          help="write a decisions template for the latest run's ambiguities")
    tmpl.add_argument("run_dir", help="artifact dir from a previous ide run")
    tmpl.add_argument("--out", default="decisions.json")
    return p


def _apply_overrides(cfg: Config, args) -> Config:
    if getattr(args, "max_tests_per_claim", None):
        cfg.max_tests_per_claim = args.max_tests_per_claim
    if getattr(args, "suite_timeout_seconds", None):
        cfg.suite_timeout_seconds = args.suite_timeout_seconds
    if getattr(args, "test_timeout_seconds", None):
        cfg.test_timeout_seconds = args.test_timeout_seconds
    return cfg


def _signatures_from_args(args) -> str:
    blocks = []
    sig_path = getattr(args, "signatures_file", None)
    if sig_path:
        blocks.append(Path(sig_path).read_text(encoding="utf-8"))
    diff_path = getattr(args, "diff_file", None)
    if diff_path:
        diff_text = Path(diff_path).read_text(encoding="utf-8", errors="replace")
        fns = extract_signatures_from_diff(diff_text)
        block = signatures_prompt_block(fns)
        if not block:
            print("[ide] note: no Python function signatures found in diff",
                  file=sys.stderr)
        elif block:
            blocks.append(block)
    return "\n\n".join(b for b in blocks if b.strip())


def _cmd_run(args) -> int:
    cfg = _apply_overrides(Config.from_env(), args)
    try:
        llm = make_client(cfg)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE

    try:
        task_description = resolve_task_description(args)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE

    decisions_file = None
    if getattr(args, "decisions_file", None):
        decisions_file = Path(args.decisions_file)
        try:
            from .decisions import load_decisions
            load_decisions(decisions_file)
        except (DecisionsError, OSError, json.JSONDecodeError) as e:
            print(f"error: invalid decisions file: {e}", file=sys.stderr)
            return EXIT_SYSTEM_FAILURE

    result = run_pipeline(
        llm=llm,
        task_description=task_description,
        project_dir=Path(args.project_dir).resolve(),
        out_dir=Path(args.out_dir),
        signatures=_signatures_from_args(args),
        cfg=cfg,
        decisions_file=decisions_file,
        seed=getattr(args, "seed", None),
    )
    print(result.report_markdown)
    print(f"\n--- artifacts: {result.artifacts_dir} ---", file=sys.stderr)
    print(f"exit {result.exit_code}: {EXIT_LABELS[result.exit_code]}", file=sys.stderr)
    usage = result.usage.get("llm_usage") or {}
    if usage:
        line = f"llm tokens: {usage}"
        cost = result.usage.get("estimated_cost_usd")
        if cost is not None:
            line += f", est. ${cost:.4f}"
        print(line, file=sys.stderr)
    if result.usage.get("coverage_stability_drop"):
        print("warning: coverage dropped vs previous run on this intent "
              "(see report)", file=sys.stderr)
    return result.exit_code


def _cmd_replay(args) -> int:
    try:
        result = replay_run(Path(args.project_dir).resolve(), Path(args.run_dir))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE
    print(result.report_markdown)
    print(f"\nexit {result.exit_code}: replay complete "
          f"(divergence={'yes' if result.exit_code == EXIT_DIVERGENCE else 'no'})",
          file=sys.stderr)
    return result.exit_code


def _cmd_post_comment(args) -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("error: set GITHUB_TOKEN to post comments.", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE
    report_path = Path(args.report)
    if not report_path.exists():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE
    body = COMMENT_MARKER + "\n\n" + report_path.read_text(encoding="utf-8")

    api = f"https://api.github.com/repos/{args.repo}/issues/{args.pr}/comments"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "intent-divergence-engine",
        "Content-Type": "application/json",
    }

    comment_id = None
    req = urllib.request.Request(api, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        for c in json.loads(resp.read().decode("utf-8")):
            if c.get("body", "").startswith(COMMENT_MARKER):
                comment_id = c["id"]
                break

    if comment_id and args.update:
        url = f"https://api.github.com/repos/{args.repo}/issues/comments/{comment_id}"
        data = json.dumps({"body": body}).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
        action = f"updated comment {comment_id}"
    else:
        data = json.dumps({"body": body}).encode()
        request = urllib.request.Request(api, data=data, headers=headers, method="POST")
        action = "posted new comment"
    with urllib.request.urlopen(request, timeout=30):
        pass
    print(f"{action} on {args.repo}#{args.pr}", file=sys.stderr)
    return EXIT_OK


def _cmd_decisions_template(args) -> int:
    from .extract import load_spec
    run_dir = Path(args.run_dir)
    specs = sorted(run_dir.glob("intentspec.v*.json"))
    if not specs:
        print(f"error: no intentspec in {run_dir}", file=sys.stderr)
        return EXIT_SYSTEM_FAILURE
    spec = load_spec(specs[-1])
    ambiguous = [c for c in spec.claims if c.ambiguous]
    if not ambiguous:
        print("No ambiguous claims in this run — nothing to decide.", file=sys.stderr)
        return EXIT_OK
    write_decisions_template(spec, Path(args.out))
    print(f"Wrote {args.out}: edit it to pick one branch per claim, then re-run "
          "with --decisions.", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": _cmd_run, "replay": _cmd_replay,
                "post-comment": _cmd_post_comment,
                "decisions-template": _cmd_decisions_template}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
