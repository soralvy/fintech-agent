---
name: project-review
description: Independently review the current repository changes against the project contracts, architecture, security invariants, and quality gates.
disable-model-invocation: true
context: fork
agent: general-purpose
model: opus
effort: high
background: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git status --short)
  - Bash(git diff HEAD)
  - Bash(git diff --stat HEAD)
  - Bash(git diff --check HEAD)
  - Bash(uv lock --check)
  - Bash(uv run ruff format --check .)
  - Bash(uv run ruff check .)
  - Bash(uv run mypy app tests)
  - Bash(uv run pytest)
disallowed-tools:
  - Write
  - Edit
---

# Independent project review

Act exclusively as an independent reviewer.

Do not edit files, apply fixes, install or update dependencies, modify the
lockfile, stage changes, create commits, push, or run mutating shell commands.

## Load the review contract

Read:

1. `CLAUDE.md`
2. `${CLAUDE_SKILL_DIR}/references/review-policy.md`
3. `docs/SPEC.md`
4. `docs/DECISIONS.md`
5. `docs/TECH_BASELINE.md`
6. `docs/TASKS.md`
7. `pyproject.toml`

The project documents and pinned configuration are authoritative. Generic
best practices must not override an explicit project decision.

If the documents contradict the repository, report the contradiction instead
of silently choosing one side.

## Capture the initial state

Run:

- `git status --short`
- `git diff --stat HEAD`
- `git diff HEAD`
- `git diff --check HEAD`
- `uv lock --check`

Review all staged, unstaged, and untracked source, test, migration,
configuration, and documentation files.

If there are no reviewable changes, report that and stop.

## Run the repository gates

Run the quality commands documented in `CLAUDE.md`.

For the current repository baseline these are:

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy app tests`
- `uv run pytest`

Do not use real credentials or call live OpenAI, embedding, market-data, or
other external services.

Report command failures as gate failures. Do not restate every Ruff or mypy
diagnostic as a separate semantic finding.

## Review the changed behavior

Review the changed code and enough surrounding code to understand:

- callers and callees;
- public schemas and interfaces;
- database and transaction boundaries;
- resource lifecycle;
- graph states and transitions;
- retrieval and citation construction;
- MCP planning and execution;
- error handling and logging;
- tests for the changed behavior.

Apply the rules in `review-policy.md`.

Review the actual execution path rather than isolated changed lines.

## Evidence requirement

Before reporting a finding:

1. Identify the exact changed behavior that introduces it.
2. Trace a concrete execution path.
3. Search for an existing safeguard or test.
4. Cite exact `file:line` locations.
5. Explain the observable impact.
6. Suggest the smallest correction and a test that would prove it.

Do not report a speculative concern as a bug.

Place unverified concerns under `Questions`. Questions do not fail the review.

## Severity

- `P0 Critical`: credential exposure, arbitrary code execution, unauthorized
  write access, destructive data loss, or externally exploitable vulnerability.
- `P1 Blocking`: incorrect behavior, broken project contract, fabricated
  evidence, unsafe tool execution, resource leak, regression, or failed
  mandatory gate.
- `P2 Important`: verified reliability or maintainability defect likely to
  cause a future failure, or missing coverage for a changed critical path.
- `P3 Nit`: non-blocking issue not already enforced by automation.

Cap P3 findings at three. Never block on style preferences or hypothetical
future requirements.

## Verify reviewer integrity

At the end, run `git status --short` again.

Compare it with the initial status. If the review process changed tracked or
untracked repository files, report reviewer integrity failure and do not mark
the changes ready.

## Output

Return these sections:

### Verdict

Use exactly one:

- `PASS`
- `PASS WITH NITS`
- `FAIL`

`FAIL` requires a verified P0/P1 finding or failed mandatory gate.

### Scope reviewed

List changed files and the inferred milestone.

### Quality gates

For every command show `PASS`, `FAIL`, or `NOT RUN`, with a concise reason.

### Findings

Sort by severity. Each finding must contain:

- severity;
- confidence: high or medium;
- `file:line`;
- violated contract or invariant;
- evidence and execution path;
- impact;
- smallest correction;
- required regression test.

If none exist, say `No verified findings`.

### Questions

List only concerns that could not be verified.

### Coverage gaps

List meaningful missing tests for changed behavior.

### Recommendation

State whether the changes are ready to commit and what must happen first.

Do not modify the repository.