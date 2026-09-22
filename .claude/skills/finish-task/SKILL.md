---
name: finish-task
description: Finish a task through a bounded loop of independent Opus review, automatic correction of non-controversial findings, and re-verification. Manually invoked only.
argument-hint: "[scope, e.g. docs/jev | milestone-3; default: current uncommitted diff]"
disable-model-invocation: true
model: sonnet
effort: high
allowed-tools:
  - Read
  - Grep
  - Glob
  - Edit
  - Write
  - AskUserQuestion
  - "Skill(project-review)"
  - "Bash(git status *)"
  - "Bash(git diff *)"
  - "Bash(git log *)"
  - "Bash(git show *)"
  - "Bash(git ls-files *)"
  - "Bash(git grep *)"
  - "Bash(git rev-parse *)"
  - "Bash(git merge-base *)"
  - "Bash(git branch --show-current)"
  - "Bash(uv lock --check)"
  - "Bash(uv run ruff format --check .)"
  - "Bash(uv run ruff format *)"
  - "Bash(uv run ruff check .)"
  - "Bash(uv run ruff check --fix *)"
  - "Bash(uv run mypy app tests)"
  - "Bash(uv run mypy app tests evals)"
  - "Bash(uv run pytest)"
  - "Bash(uv run pytest *)"
disallowed-tools:
  - "Bash(git add *)"
  - "Bash(git commit *)"
  - "Bash(git push *)"
  - "Bash(git stash *)"
  - "Bash(git switch *)"
  - "Bash(git checkout *)"
  - "Bash(git restore *)"
  - "Bash(git reset *)"
  - "Bash(git clean *)"
  - "Bash(git rebase *)"
  - "Bash(git merge *)"
  - "Bash(gh pr *)"
  - "Bash(uv add *)"
  - "Bash(uv remove *)"
  - "Bash(uv lock)"
  - "Bash(uv lock --upgrade *)"
  - "Bash(uv sync *)"
  - "Bash(uv pip *)"
---

# Finish task

Requested scope:

`$ARGUMENTS`

Run this workflow inline in the current session. You orchestrate and fix;
the independent reviewer is the `project-review` skill, which runs forked on
Opus and returns its result directly to you. Never imitate the reviewer in
this context.

## Hard limits

- Never commit, stage, stash, push, or open, edit, approve, close, or merge a
  pull request. Publishing belongs to `/git-workflow`.
- Never run destructive Git commands or discard, revert, or overwrite
  unrelated user work.
- Never modify dependencies or `uv.lock`.
- Never bypass tests, hooks, permission prompts, or repository policy, and
  never weaken a check (skip markers, `type: ignore`, lint exclusions,
  loosened assertions) to make it pass.
- If the user rejects a command, stop at that boundary and report it.
- Never claim a command passed unless it completed successfully in this run.

## 1. Establish scope

Read `CLAUDE.md`, then run `git status --short`, `git diff --stat HEAD`,
`git diff HEAD`, and `git ls-files --others --exclude-standard`. Read the
relevant parts of `docs/SPEC.md`, `docs/DECISIONS.md`, `docs/TASKS.md`,
`docs/TECH_BASELINE.md`, and the implementation and tests in scope.

- Empty scope: the current uncommitted diff (staged, unstaged, untracked).
- A path or topic (`docs/jev`) or milestone (`milestone-3`): the files and
  behavior belonging to it.

Record the set of files that were already modified before you started.
Changes outside the scope are unrelated user work: leave them untouched.

State the scope being finished in one concise sentence.

## 2. Run verification

Run the gates from `CLAUDE.md` that apply to the scope. The full set is:

1. `uv lock --check`
2. `uv run ruff format --check .`
3. `uv run ruff check .`
4. `uv run mypy app tests`
5. `uv run pytest` (note the skip count)

Take the exact commands from `CLAUDE.md`; the list above is the current
baseline, not a replacement for what that file says.

For documentation- or configuration-only scopes the gates still run, since
they are cheap and prove nothing else broke. The live HTTP smoke test is
required by `CLAUDE.md` only after changes to startup, lifespan, the
database, or the HTTP contract; if the scope needs it and you cannot run it,
report that instead of claiming it.

Fix in-scope gate failures before the first review. If a gate cannot run at
all, stop and report `BLOCKED`.

## 3. Invoke project-review

Invoke the `project-review` skill through the Skill tool and wait for its
complete result. Pass as arguments:

```text
<scope from step 1>
Round: <n> of <3, or 4 when the closure round of step 5 applies>
Resolved decisions:
- <each user answer from this run, or "none">
```

Do not pass your own opinion of the findings, and do not pre-filter what the
reviewer should look at. If the skill cannot be invoked or does not return a
`VERDICT:` line, stop and report `BLOCKED`; do not substitute your own
review.

## 4. Handle the verdict

### CLEAN

Stop the loop and produce the final report.

### FIXABLE

Fix every finding with `requires_user_decision: no`:

- apply the reviewer's smallest safe fix, or an equivalent that a binding
  source of truth determines;
- keep changes inside the original scope;
- when a fix touches a file that also holds unrelated user edits, change only
  the lines the finding names;
- run `uv run ruff format` or `uv run ruff check --fix` only on the paths you
  changed, never on `.`, so unrelated files are not rewritten.

Resolve without asking when a binding source of truth or repository
convention determines the answer, when one option is strictly safer without
changing product behavior, when the change is local and readily reversible,
or when it repairs an objective contradiction, typo, stale reference, test
mismatch, or validation gap.

Treat a finding as requiring a user decision, even if the reviewer marked it
`no`, when the fix would change a public contract, security boundary,
persistence schema, or user-visible behavior without an existing binding
decision. Collect it for step "DECISION_REQUIRED".

If you believe a finding is wrong because a source of truth already resolves
it, do not silently skip it: add that source to `Resolved decisions` as
`already determined by <file:section>` for the next round so the reviewer
can confirm or reject it.

When a fix would update a repeated value such as a command, a version, or a
path, search the whole repository for other occurrences rather than trusting
the reviewer's list, and say in the fix summary what the search covered.

Then rerun the relevant verification and invoke `project-review` again.

### DECISION_REQUIRED

First fix every finding that needs no decision, as for `FIXABLE`.

Then collect every decision from the full review, and drop any whose answer
is already determined by repository evidence. Ask the user once, using
AskUserQuestion with at most three grouped questions. For each question, put
the recommended option first, labelled `(Recommended)`, and state the
material trade-off in its description.

Never ask sequential questions that could have been asked together. After
the user answers, apply all answers together, record them under
`Resolved decisions`, rerun verification, and invoke `project-review` again.

### BLOCKED

Stop. Report the exact missing evidence or unavailable command. Do not
describe the task as reviewed.

## 5. Bounded loop

A round is one `project-review` invocation plus the fixes that follow it.

The normal budget is three reviews and two fix passes:

- Review 1 → Fix 1
- Review 2 → Fix 2
- Review 3 → the final result in the normal case

Every fix is always followed by a review, so no change ever ships unreviewed.

### Conditional closure round

If Review 3 returns `FIXABLE`, you may extend the loop by exactly one closure
round — Fix 3, relevant verification, then Review 4 — but only when **every**
remaining finding satisfies all of:

- it requires no user decision;
- it has concrete evidence;
- its smallest fix is objective;
- it stays inside the original scope;
- it changes no product behavior, public contract, security boundary,
  persistence schema, dependency, or architecture.

If any remaining finding fails any of these, do not extend: stop at Review 3
and report.

Review 4 is always final. Never apply a fix after Review 4, whatever it
returns. Never use the extension for `DECISION_REQUIRED`, `BLOCKED`,
speculative findings, or scope expansion.

The absolute limits are therefore four reviews, three fix passes, and no
unreviewed fixes.

### Stopping early

Stop before the budget is spent when:

- the verdict is `CLEAN`;
- only P3 findings remain;
- a finding needs user authority that the user has not given;
- verification cannot run;
- a proposed fix would materially expand the task beyond its scope.

When the final review is not `CLEAN`, report all remaining P0–P2 findings
together and stop.

## 6. Final report

Return only these sections, and nothing speculative or optional:

```text
FINAL VERDICT: CLEAN | FIXABLE | DECISION_REQUIRED | BLOCKED
SCOPE: <one sentence>
FILES CHANGED BY THIS RUN:
- path — what changed
VERIFICATION:
- command — PASS | FAIL | NOT RUN, result (skip counts for pytest)
REVIEW ROUNDS: <n> of 4 (fix passes: <m> of 3)
UNRESOLVED P0–P2 FINDINGS:
- ... (or "none")
```

Then close with exactly one of the following, matching the verdict:

- `CLEAN` — print:

  ```text
  NEXT_COMMAND: /git-workflow publish
  ```

- `FIXABLE` at the absolute review limit — print `NEXT_ACTION:` followed by
  each remaining finding with its `file:line` and its smallest fix. Print no
  command.
- `DECISION_REQUIRED` — print the bundled questions. Print no command.
- `BLOCKED` — print the concrete condition that would unblock the work. Print
  no command.

Rules for this section:

- Never invent a placeholder, no-op, illustrative, or `# TODO` command, and
  never pad a command line to make the format look complete.
- Print `NEXT_COMMAND` only when there is a single real command the user can
  run, and only in the `CLEAN` case.
- Every `NEXT_COMMAND` must be directly executable exactly as printed.
- Never print a command and then correct, retract, or amend it later in the
  report. If you are unsure a command is right, resolve it before printing.
