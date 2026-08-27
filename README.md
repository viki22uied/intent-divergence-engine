# Intent Divergence Engine (IDE)

Checks AI-generated code against the intent that produced it. Not style. Not
security patterns. One question: **does the code do what the requester asked
for** — including the cases they meant but never stated.

Licensed under the [MIT License](LICENSE).

## How it works

Five stages, each with a strict input/output contract:

| Stage | What it does | Requirements |
|---|---|---|
| 1. Intent Extraction | Task text → structured `IntentSpec` of testable claims, with stated-vs-inferred provenance and a fixed ambiguity checklist (ordering, duplicates, empty/null input, off-by-one, concurrency/idempotency, error handling). Ambiguities are recorded as competing branches, never silently resolved. | R1.1–R1.4 |
| 2. Test Synthesis | One example test per precondition/postcondition, a Hypothesis property test per invariant, one separate test file per ambiguity branch. Every test name carries its claim ID; untraceable tests are dropped and counted. | R2.1–R2.5 |
| 3. Execution | Sandboxed subprocess (env allowlist, secret-pattern refusal), hard timeouts per test and suite, JUnit XML capture of inputs/actual/expected per failure. | R3.1–R3.4 |
| 4. Divergence Reporting | Coverage summary first, then Confirmed Divergent (plain-language: claim, input, actual, expected), Unresolved/Ambiguous side-by-side with no default answer, Confirmed Correct last. "Verified correct" language is structurally scrubbed. | R4.1–R4.4 |
| 5. Coverage Reporting | Claims tested vs total, untestable claims listed with reasons, shown before pass results so low coverage can't masquerade as correctness. | R5.1–R5.3 |

## Install

```bash
pip install .
```

Requires Python 3.10+. Generated suites run under pytest + pytest-timeout +
Hypothesis (installed automatically).

## Configure

Any OpenAI-compatible endpoint:

```bash
export IDE_LLM_BASE_URL="https://api.openai.com/v1"   # or a local server
export IDE_LLM_API_KEY="sk-..."
export IDE_LLM_MODEL="gpt-4o-mini"
# optional cost estimate (R9.5):
export IDE_PRICE_PER_1M_INPUT_TOKENS=0.15
export IDE_PRICE_PER_1M_OUTPUT_TOKENS=0.60
```

Cost/cap knobs (R9.5): `IDE_MAX_TESTS_PER_CLAIM`, `IDE_MAX_HYPOTHESIS_EXAMPLES`,
`IDE_SUITE_TIMEOUT_SECONDS`, `IDE_TEST_TIMEOUT_SECONDS`, `IDE_MAX_CLAIMS` (default 25),
`IDE_MAX_TASK_CHARS` (default 8000), `IDE_LLM_MAX_TOKENS` (default 2000),
`IDE_MEMORY_LIMIT_MB` (default 1024, enforced via psutil; logged if unavailable),
`IDE_SANDBOXED` / `IDE_ALLOW_UNSANDBOXED` (see Security).

## CLI usage (R8.3 local flow)

```bash
# from the repo containing the AI-generated change:
ide run \
  --task-file ticket.txt \
  --diff-file change.diff \        # auto-extracts changed signatures (feeds Stage 2)
  --project-dir . \
  --out-dir .ide/runs/latest

# or use the PR description itself as the intent source (R8.2, one source per run):
ide run --github-pr "owner/repo#42" --project-dir .
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | no divergence found |
| 1 | confirmed divergences found (blocks in CI) |
| 2 | ambiguous intent needs a human decision |
| 3 | engine system failure — never blocks a PR by default (R9.1) |

Artifacts land in `--out-dir`: `intentspec.v*.json` (versioned intent, R1.4),
`generated_tests/`, `claim_map.json`, `report.md`, `coverage_history.json`
(M5 stability), `run_meta.json` (seed + LLM tokens + estimated cost,
R9.3/R9.5), and peak-memory/duration per suite in the report (R3.4).

## Ambiguity decision loop (R4.2)

```bash
ide decisions-template .ide/runs/latest --out decisions.json
$EDITOR decisions.json          # {"C2": "C2.b"}  <- pick one branch per claim
ide run --task-file ticket.txt --decisions decisions.json ...
```

Undecided claims stay ambiguous and keep per-branch tests; decided claims are
re-checked as resolved, with the decision recorded in the IntentSpec.

## Replay / auditability (R9.3)

Hypothesis seeds are pinned at synthesis and logged. Re-run a stored run's
tests against current code without any LLM calls:

```bash
ide replay .ide/runs/latest --project-dir .
```

## Benchmark harness (M1/M2/M3)

Labeled cases live in `benchmark/cases/` (each: task.txt, under_test.py with a
seeded "almost right" defect, labels.json). Run offline against canned payloads
or with a live LLM:

```bash
python benchmark/run_benchmark.py benchmark/cases --offline
```

Outputs aggregate M2 recall, M1 false-positive count and a
`findings_for_review.csv` for human precision review.

## GitHub Action (R8.3 CI flow)

> **Security: do not use `pull_request_target`.**
> `pull_request_target` runs with a write-scoped `GITHUB_TOKEN` and your repo
> secrets in the context of untrusted fork content. Combined with any tool that
> turns PR text into executed code, that grants an external PR secret
> exfiltration and repo write. Use `pull_request` (read-only token for fork PRs).
> If you need write permission for PR comments from forks, use the
> `workflow_run` two-workflow pattern. The action hard-fails on
> `pull_request_target` unless you explicitly set `force-pull-request-target`.

`.github/workflows/ide.yml`:

```yaml
name: ide
on: [pull_request]   # not pull_request_target
permissions:
  pull-requests: write
  contents: read
jobs:
  check-intent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: your-org/intent-divergence-engine@v1
        with:
          task-file: .github/ticket.txt   # or task-text: "..."
          github-token: ${{ secrets.GITHUB_TOKEN }}
        env:
          IDE_LLM_API_KEY: ${{ secrets.IDE_LLM_API_KEY }}
          # To run generated tests inside an isolated container:
          # IDE_SANDBOXED: "1"
```

The action posts/updates a single marker-identified PR comment with the report
(R4.3), runs shell commands via env-var indirection (no `${{ inputs.* }}`
splicing), and fails the check only on exit code 1. To execute LLM-generated
tests with network/filesystem isolation, run the `ide` step inside a
`--network none` container and set `IDE_SANDBOXED=1` (see `README` Security
section below).

## Example report shape

```
# Intent Divergence Report
## Coverage Summary            <- always first (R5.3)
## Confirmed Divergent (n)     <- claim / input / actual / expected per finding
## Unresolved / Ambiguous      <- branch table, decision required (R4.2)
## Confirmed Correct (n)
## Method Notes                <- edge categories targeted vs fuzzed (R2.4), caps
```

## Security

**Trust boundary:** the PR author is untrusted. Their words flow through an LLM
into Python files that are then executed. This is the system's central risk.

- **What we do:** (1) env-var allowlist + secret-name denylist, wall-clock
  timeouts, (2) AST safety gate that **rejects** generated files importing
  `os`/`sys`/`subprocess`/`socket`/`urllib`/etc. or calling `eval`/`exec`/
  `__import__`/`open` — rejected files are never executed and are reported as
  `blocked: disallowed import`, (3) shell-injection safe GitHub Action (all
  `inputs.*` via env-var indirection, `pull_request_target` hard-fail).
- **What filtering cannot do:** denylists are bypassable
  (`__import__('os')`, `getattr`, etc.). The gate is defense-in-depth only.
- **What you must do for hostile PRs:** run `ide` inside a rootless container
  or microVM with `--network none`, a read-only checkout mount, no credentials
  mounted, and set `IDE_SANDBOXED=1` (or `IDE_ALLOW_UNSANDBOXED=1` to acknowledge
  the risk and proceed on the host). Without this, generated code runs with full
  host privileges (see `src/intent_ide/safety.py`).

## Scope & honesty

- v1 targets Python code under test (R8.1).
- Claims needing live external systems are reported untestable with a reason,
  never skipped silently (R8.4, R5.2).
- The engine does not generate code, does not prove correctness, and never
  claims to (NG2, R4.4). It reports what was tested.
- V1.1 adds an AST pre-execution gate, but filtering is not a sandbox — for
  hostile code, run inside a container/job with no network and no credentials
  (R9.4). See Security section above.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests -q
```

All tests run offline against a scripted fake LLM — no API key needed.
