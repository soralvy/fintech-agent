---
name: start-task
description: Start a new implementation task on a safe feature branch before any repository file is edited. Invoke automatically when the user asks to begin or implement a new milestone, feature, fix, refactor, documentation task, test task, CI task, or issue. Do not invoke for read-only analysis, review, questions, or continuation of work already on the matching task branch.
when_to_use: Use before the first repository mutation for a new implementation scope. Infer a concise branch slug from an explicitly named task and pass it as the argument; ask one question only when the scope cannot be named safely. Pass `--carry` only when the user explicitly asked for it.
argument-hint: "<slug | type/slug> [--carry]"
context: fork
agent: general-purpose
background: false
model: sonnet
effort: high
allowed-tools:
  - Read
  - "Bash(git status *)"
  - "Bash(git branch --show-current)"
  - "Bash(git branch --list *)"
  - "Bash(git branch -r --list *)"
  - "Bash(git rev-parse *)"
  - "Bash(git rev-list *)"
  - "Bash(git check-ref-format *)"
  - "Bash(git log *)"
  - "Bash(git remote -v)"
disallowed-tools:
  - Edit
  - Write
  - NotebookEdit
  - "Bash(git add *)"
  - "Bash(git commit *)"
  - "Bash(git push *)"
  - "Bash(git stash *)"
  - "Bash(git reset *)"
  - "Bash(git restore *)"
  - "Bash(git checkout *)"
  - "Bash(git clean *)"
  - "Bash(git merge *)"
  - "Bash(git rebase *)"
  - "Bash(git pull *)"
  - "Bash(git branch -d *)"
  - "Bash(git branch -D *)"
  - "Bash(git branch --delete *)"
  - "Bash(git branch -m *)"
  - "Bash(git tag *)"
  - "Bash(gh *)"
---

# Start task

Requested task:

`$ARGUMENTS`

This workflow runs as a blocking forked subagent. The caller waits for your
report, and your tool restrictions end when you return. You cannot see the
caller's conversation: act only on the arguments above, and never infer
`--carry`. Your only state changes are one `git fetch` and one `git switch`,
each a separate Bash call. Project settings list both under
`permissions.ask`, so interactive permission modes prompt for them; other
modes, such as `dontAsk` or `bypassPermissions`, may not, so do not tell the
user that a confirmation happened unless one did. Never commit, push, stash,
reset, restore, clean, merge, rebase, pull, tag, or delete or rename a branch,
and never edit a file.

If the user rejects a command, stop at that boundary and report it. Do not
retry through an equivalent command.

## 1. Parse the arguments

- The first argument is required: `<slug>` or `<type>/<slug>`.
  - `type` is one of `feat`, `fix`, `docs`, `chore`, `test`, `refactor`,
    `ci`. A bare slug means `feat/<slug>`.
  - `slug` matches `^[a-z0-9]+(-[a-z0-9]+)*$` and is at most 60 characters.
- The optional `--carry` flag permits carrying uncommitted changes onto the new
  branch (step 3).
- Anything else, or a missing slug: stop and show
  `/start-task <slug | type/slug> [--carry]`.

Confirm the name with `git check-ref-format --branch <type>/<slug>`.

## 2. Inspect, read-only

Run:

- `git rev-parse --show-toplevel` (stop if this is not a Git repository);
- `git branch --show-current`;
- `git status --porcelain=v2 --branch --untracked-files=all`;
- `git rev-parse --git-path MERGE_HEAD`, `--git-path rebase-merge`,
  `--git-path rebase-apply`, `--git-path CHERRY_PICK_HEAD`, then `Read` or
  `git status` to tell whether any of them exists;
- `git remote -v` (an `origin` remote must exist);
- `git branch --list <type>/<slug>`.

## 3. Refuse ambiguous or unsafe states

Stop, change nothing, and report the exact condition when any of these holds:

1. a merge, rebase, or cherry-pick is in progress;
2. `HEAD` is detached;
3. the current branch is not exactly `main`. Task branches start from
   `main`; say which branch is checked out and let the user decide;
4. a local branch `<type>/<slug>` already exists;
5. the working tree has staged, unstaged, or untracked changes and `--carry`
   was not given. List the paths and explain that `--carry` moves them onto
   the new branch;
6. `--carry` was given but local `main` differs from `origin/main` after the
   fetch (step 4), because carried changes must sit on the exact base the
   branch starts from.

## 4. Relate to origin/main

Explain that the next command updates only remote-tracking refs: it refreshes
every `origin/*` branch, including `origin/main` and any existing
`origin/<type>/<slug>`, and prunes deleted ones. Then request exactly one
separate fetch:

```text
git fetch --prune origin
```

Only after that fetch, run, read-only:

- `git rev-list --left-right --count main...origin/main`;
- `git branch -r --list origin/<type>/<slug>`.

Stop when:

- local `main` has commits that `origin/main` lacks (left count above zero).
  Local-only work on the default branch must be resolved by the user;
- `origin/<type>/<slug>` already exists.

Local `main` being behind `origin/main` is fine: the new branch starts from
`origin/main`, and local `main` is left untouched.

## 5. Create and switch

Explain the command, then request exactly one of:

- clean tree: `git switch --no-track -c <type>/<slug> origin/main`;
- `--carry` (local `main` equals `origin/main`, checked in step 3):
  `git switch --no-track -c <type>/<slug>`.

`--no-track` keeps `origin/main` from becoming the new branch's upstream;
`/git-workflow` sets the upstream on the first push.

## 6. Report

Run `git branch --show-current` and `git status --short`, then report:

```text
BRANCH: <type>/<slug>
BASE: origin/main @ <short sha>
CARRIED CHANGES: <paths, or "none">
STATUS:
<git status --short output, or "clean">
NEXT: inspect the binding documents for this scope and plan the change before implementation. Consult docs/DECISIONS.md section 4 when adding, moving, or changing module responsibilities.
```
