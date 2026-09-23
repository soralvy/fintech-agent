---
name: git-workflow
description: Use whenever the user asks to inspect Git changes, create or plan commits, push a branch, open or update a pull request, or mark a PR ready. Trigger for requests such as "commit this", "push this", "open PR", "create a pull request", and equivalent wording.
argument-hint: "[inspect|commit|pr|publish|ready]"
disable-model-invocation: false
model: sonnet
effort: high
allowed-tools:
  - Read
  - Grep
  - Glob
  - "Bash(git status *)"
  - "Bash(git diff *)"
  - "Bash(git log *)"
  - "Bash(git show *)"
  - "Bash(git rev-parse *)"
  - "Bash(git rev-list *)"
  - "Bash(git merge-base *)"
  - "Bash(git branch --show-current)"
  - "Bash(git branch --list *)"
  - "Bash(git remote -v)"
  - "Bash(git ls-files *)"
  - "Bash(git symbolic-ref *)"
  - "Bash(git config --get *)"
  - "Bash(gh auth status)"
  - "Bash(gh repo view *)"
  - "Bash(gh pr list *)"
  - "Bash(gh pr status *)"
  - "Bash(gh pr view *)"
  - "Bash(gh pr checks *)"
  - "Bash(uv run python scripts/verify.py)"
---

# Git workflow

Requested operation:

`$ARGUMENTS`

Run this workflow inline in the current session. Do not delegate it to a
subagent.

## Modes

Interpret the first argument as the mode:

- Empty or `inspect`: inspect changes and propose a commit/PR plan. Do not
  mutate Git state.
- `commit`: create locally verified commits. Do not push.
- `pr`: push already committed changes and create or update a draft PR.
- `publish`: create commits, push them, and create or update a draft PR.
- `ready`: verify the existing PR and mark it ready for review.

For an unknown mode, stop and show the supported invocations.

## Permission contract

Read-only inspection and the listed verification commands may run without
additional approval.

Every state-changing command must be a separate Bash tool call so the user
can approve or reject it independently. This includes:

- `git fetch`
- `git add`
- `git commit`
- `git push`
- `gh pr create`
- `gh pr edit`
- `gh pr ready`

Never combine state-changing commands using `&&`, `;`, pipes, subshells,
command substitution, loops, aliases, wrappers, or `git -C`.

Before requesting a state-changing command, explain briefly:

1. what the command changes;
2. the exact branch, paths, commit message, or PR affected;
3. why it is needed.

If the user rejects a command, stop at that boundary. Do not retry through
an equivalent command or workaround.

Do not edit application code, tests, documentation, or configuration while
running this skill. Report failures back to the implementation workflow.

## Repository preflight

Before planning anything:

1. Read `CLAUDE.md`.
2. Read applicable repository instructions and the existing PR template.
3. Inspect:
   - current branch;
   - default branch;
   - remotes;
   - upstream tracking;
   - staged, unstaged, and untracked files;
   - recent commit-message style;
   - commits ahead of or behind the default branch.
4. Inspect the complete diff, including relevant untracked files.
5. Identify unrelated user changes and preserve them.
6. Check for accidental secrets, `.env` files, credentials, database dumps,
   generated artifacts, or unexpected binary files. Never print secret
   values.

Do not continue if the directory is not a Git repository or if the intended
scope cannot be separated safely.

## Branch policy

Protected-branch preflight, for every mode that changes Git state (`commit`,
`pr`, `publish`, `ready`): run `git branch --show-current`. If it prints
`main`, `master`, the repository's default branch, or nothing (detached
`HEAD`), stop before any state change. Tell the user to run
`/start-task <slug> --carry`, which moves uncommitted work onto a new task
branch, and to rerun this skill there. This skill never creates or switches
branches. The edit hook is early protection, not the only one.

If the default branch already contains local-only commits, stop. Do not
rewrite or move them automatically.

Never run `git pull`, merge, rebase, cherry-pick, reset, restore, clean,
stash, amend, tag, or delete branches.

## Commit planning

Group changes into the smallest number of complete, reversible commits.

Rules:

- A commit must represent one coherent intent.
- Keep implementation and its directly related tests together.
- Keep a dependency declaration and its lockfile update together.
- Do not split changes merely to produce more commits.
- Do not mix unrelated documentation, tooling, refactors, and behavior.
- If one file contains inseparable unrelated changes, stop and explain the
  conflict. Do not generate index patches automatically.
- Respect the existing repository message convention.
- If there is no established convention, use:
  `<type>: <short imperative summary>`.
- Prefer `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, and `ci`.
- Keep the subject concise. Add a body only when the reason or trade-off is
  not clear from the subject.

Before mutation, show the complete proposed commit plan:

- commit order;
- exact paths in each commit;
- proposed message;
- verification associated with the change.

## Verification

Use repository-specific gates from `CLAUDE.md` and project documentation.

Run the canonical full gate named in `CLAUDE.md`,
`uv run python scripts/verify.py`, which fails on any skipped test or a
missing `TEST_DATABASE_URL`. Also confirm any milestone-specific live check
that `CLAUDE.md` requires was actually run; this skill does not run it.

Do not run auto-fix commands in this skill.

Never state that a check passed unless that exact command completed
successfully. If a required check fails, stop before committing or
publishing and report:

- failing command;
- concise failure summary;
- whether the failure appears related to the current changes.

## Creating commits

For each approved commit:

1. Stage only explicit paths using `git add -- <path...>`.
2. Never use `git add .`, `git add -A`, `git commit -a`, or wildcard staging.
3. Run `git diff --cached --check`.
4. Review `git diff --cached`.
5. Confirm that only the planned paths and hunks are staged.
6. Request approval for `git commit`.
7. Inspect the created commit using `git show --stat --oneline HEAD`.
8. Check repository status before continuing.

Do not use `--no-verify`, `--amend`, or disable signing or hooks.

After all commits, rerun the required verification gates before push.

## Publishing

Before a push or PR:

1. Require a non-default feature branch.
2. Require successful verification.
3. Check `gh auth status`.
4. Determine the GitHub default branch.
5. Request approval for a separate:
   `git fetch --prune origin <base>`.
6. Recalculate branch divergence.
7. If the result is suspicious or conflicting, stop. Never merge or rebase
   automatically.
8. Check whether an open PR already exists for the branch.

Push with an explicit branch:

- first push: `git push -u origin <branch>`;
- later pushes: `git push origin <branch>`.

Never force push and never push to the default branch.

## Pull requests

If an open PR already exists:

- do not create a duplicate;
- push approved commits;
- inspect whether the title or body is stale;
- request separate approval before using `gh pr edit`.

For a new PR:

1. Generate and show the complete proposed title and body.
2. Create it as a draft unless the user explicitly selected `ready`.
3. Push the branch before PR creation.
4. Pass both `--base` and `--head` explicitly.
5. Send the body through standard input using `--body-file -`.
6. Do not use `gh pr create --dry-run`.

Use this body structure:

## Summary

- two to four concrete bullets

## Why

Explain the problem or milestone this PR addresses.

## Changes

Describe the meaningful implementation changes.

## Verification

List only commands that actually ran and their results.

## Risks and limitations

Describe known risks, deferred work, or `None identified`.

## Review guidance

Point the reviewer to the most important files or decisions.

Reference an issue only when a real issue number was found. Never invent
issue links.

After creation, inspect the PR URL, number, title, draft state, base, and
head. Run `gh pr checks`. Treat exit code 8 as pending checks, not a failed
check.

## Ready mode

`ready` may mark an existing draft PR ready only when:

- the working tree contains no unintended changes;
- all required gates pass;
- the PR diff has been self-reviewed;
- no known blocker from `/project-review` remains;
- the PR body accurately describes the current branch.

Request separate approval for `gh pr ready`.

Never merge, approve, close, or delete the PR.

## Final report

Report:

- operation completed;
- branch and base branch;
- commits created;
- files intentionally left uncommitted;
- verification commands and results;
- push result;
- PR URL and draft/ready state;
- CI state;
- blockers or remaining manual steps.

Do not claim success for any command the user rejected or that did not run.