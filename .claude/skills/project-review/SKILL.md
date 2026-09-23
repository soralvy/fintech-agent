---
name: project-review
description: Independent, read-only review of repository changes against the project contracts, architecture, security invariants, and quality gates. Use when the user explicitly asks for a project review, or when `/finish-task` requests an independent review.
argument-hint: "[scope, e.g. docs/jev | milestone-3; default: current uncommitted diff]"
disable-model-invocation: false
context: fork
agent: general-purpose
model: opus
effort: high
background: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - "Bash(git status *)"
  - "Bash(git diff *)"
  - "Bash(git log *)"
  - "Bash(git show *)"
  - "Bash(git ls-files *)"
  - "Bash(git rev-parse *)"
  - "Bash(git merge-base *)"
  - "Bash(git branch --show-current)"
  - "Bash(uv run python scripts/verify.py)"
  - "Bash(uv lock --check)"
  - "Bash(uv run ruff format --check *)"
  - "Bash(uv run ruff check *)"
  - "Bash(uv run mypy)"
  - "Bash(uv run mypy *)"
  - "Bash(uv run pytest)"
  - "Bash(uv run pytest *)"
disallowed-tools:
  - Edit
  - Write
  - NotebookEdit
  - Agent
  - Skill
  - "Bash(git add *)"
  - "Bash(git commit *)"
  - "Bash(git push *)"
  - "Bash(git stash *)"
  - "Bash(git switch *)"
  - "Bash(git checkout *)"
  - "Bash(git restore *)"
  - "Bash(git reset *)"
  - "Bash(git clean *)"
  - "Bash(git fetch *)"
  - "Bash(gh *)"
  - "Bash(uv add *)"
  - "Bash(uv remove *)"
  - "Bash(uv lock)"
  - "Bash(uv lock --upgrade *)"
  - "Bash(uv sync *)"
  - "Bash(uv pip *)"
  - "Bash(uv run ruff format .)"
  - "Bash(uv run ruff check --fix *)"
  - "Bash(rm *)"
  - "Bash(mv *)"
  - "Bash(cp *)"
---

# Independent project review

Requested scope:

`$ARGUMENTS`

Act exclusively as an independent reviewer. Your result returns directly to
the caller (the user or `/finish-task`), so it must be complete and
self-contained.

## Read-only contract

Do not edit, create, move, or delete files. Do not apply fixes, format code,
install or update dependencies, modify the lockfile, stage changes, stash,
switch branches, commit, push, touch pull requests, or run any other command
that mutates the repository or its Git state. Only the tools and commands
listed in this skill's frontmatter are permitted.

## Load the review contract

Read:

1. `CLAUDE.md`
2. `${CLAUDE_SKILL_DIR}/references/review-policy.md` — the single canonical
   review policy, including what is and is not reportable.
3. `docs/SPEC.md`
4. `docs/DECISIONS.md`
5. `docs/TECH_BASELINE.md`
6. `docs/TASKS.md`
7. `pyproject.toml`

The project documents and pinned configuration are authoritative. Generic
best practices must not override an explicit project decision.

If the requested scope includes a `Resolved decisions` list, treat each entry
as binding user direction. Do not re-raise it as a decision.

## Establish the scope

Run `git status --short`, `git diff --stat HEAD`, `git diff HEAD`, and
`git diff --check HEAD`. List untracked files with
`git ls-files --others --exclude-standard` and read the relevant ones.

- Empty scope: review the entire current uncommitted diff (staged, unstaged,
  and untracked).
- A path or topic (for example `docs/jev`): review the changed files and
  behavior belonging to that topic, plus whatever they depend on.
- A milestone (for example `milestone-3`): review the implementation, tests,
  and documentation for that milestone against its exit conditions in
  `docs/TASKS.md`.

If the scope cannot be resolved to concrete files or there is nothing to
review, return `BLOCKED` and say exactly what is missing.

## Run the repository gates

Run the canonical full gate named in `CLAUDE.md`:
`uv run python scripts/verify.py`. It fails when any test is skipped, and when
`TEST_DATABASE_URL` is unset. Individual Ruff, mypy, or pytest commands may be
run afterwards only to diagnose a failure; they never replace the full gate.

Do not use real credentials or call live OpenAI, embedding, market-data, or
other external services.

A failing gate is a P1 finding. Report it once, not one finding per Ruff or
mypy diagnostic. A gate that cannot run at all, including a missing
`TEST_DATABASE_URL`, makes the review `BLOCKED`.

## Review the whole scope in one pass

Assess the entire requested scope before returning. Do not stop after the
first issue, and do not hold findings back for a later run: the caller fixes
everything from one result, and each re-review must not surface problems
that were already present and discoverable in this one.

Review the actual execution path rather than isolated changed lines:
callers and callees, public schemas, database and transaction boundaries,
resource lifecycle, graph states and transitions, retrieval and citation
construction, MCP planning and execution, error handling and logging, and
the tests for the changed behavior.

Apply `review-policy.md`, including its reportability rules. Anything that
fails them is omitted entirely — not listed as a note, question, or nit.

## Evidence requirement

Before reporting a finding:

1. Identify the exact behavior or text that introduces it.
2. Trace a concrete execution path or cite the contradicting passages.
3. Search for an existing safeguard, test, or source of truth that already
   resolves it. If one exists, drop the finding.
4. Cite exact `file:line` locations.
5. State the smallest safe fix.

A concern you cannot verify with evidence is not a finding.

## Severity

- `P0 Critical`: credential exposure, arbitrary code execution, unauthorized
  write access, destructive data loss, or externally exploitable
  vulnerability.
- `P1 Blocking`: incorrect behavior, broken project contract, fabricated
  evidence, unsafe tool execution, resource leak, regression, contradiction
  between binding documents, or failed mandatory gate.
- `P2 Important`: verified reliability or maintainability defect in the
  changed scope likely to cause a failure, or a missing test for a current
  required behavior.
- `P3 Minor`: a real but non-blocking defect. At most three. P3 never changes
  the verdict.

## Decisions

A finding requires a user decision only when the fix would change a public
contract, security boundary, persistence schema, or user-visible behavior
and no binding source of truth (`SPEC.md`, `DECISIONS.md`,
`TECH_BASELINE.md`, `TASKS.md`, `CLAUDE.md`, or a resolved decision) already
determines the answer.

It does not require a decision when a source of truth or repository
convention determines the answer, when one option is strictly safer without
changing product behavior, or when it repairs an objective contradiction,
typo, stale reference, test mismatch, or validation gap.

## Verify reviewer integrity

Run `git status --short` again at the end and compare it with the initial
status. If the review changed any tracked or untracked file, return
`BLOCKED` with a reviewer-integrity failure.

## Output

Return exactly one result in this format and nothing else:

```text
VERDICT: CLEAN | FIXABLE | DECISION_REQUIRED | BLOCKED

REVIEWED_SCOPE:
- concise scope description and the files covered

FINDINGS:
- [P1] path/to/file:line — title
  broken contract or invariant: ...
  evidence: ...
  smallest safe fix: ...
  requires_user_decision: yes | no

DECISIONS:
- question, options, recommended option first, material trade-off
  (only genuine unresolved product choices; otherwise "none")

VERIFICATION:
- command — PASS | FAIL | NOT RUN, concise result (include skip counts)

STOP_REASON:
- why the review is complete, or exactly what blocked it
```

Verdict rules:

- `BLOCKED`: a required gate or command could not run, the scope could not be
  resolved, required evidence is unavailable, or reviewer integrity failed.
- `DECISION_REQUIRED`: at least one P0–P2 finding has
  `requires_user_decision: yes`. Findings that need no decision are still
  listed so the caller can fix them in the same round.
- `FIXABLE`: at least one P0–P2 finding, none requiring a decision.
- `CLEAN`: no P0, P1, or P2 findings. P3 findings may still be listed.

Write `FINDINGS: none` when there are none.
