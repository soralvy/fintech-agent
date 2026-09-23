#!/usr/bin/env bash
# PreToolUse guard for Edit, Write, and NotebookEdit.
#
# Blocks file edits inside this repository while the current branch is main or
# master, and tells Claude to start a task branch first. It never switches
# branches itself. Early protection only: /finish-task and /git-workflow run
# their own protected-branch checks.
#
# Allows the edit (exit 0) when:
# - the project directory is not a Git repository, or HEAD is detached;
# - the branch is anything other than main or master;
# - the target file is outside the project directory (for example Claude's
#   own memory or scratchpad files).
# Blocks (exit 2, message on stderr) only for an in-repository file on main or
# master. If the target path cannot be read from the hook input, it is treated
# as inside the repository.

set -u

project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"

branch="$(git -C "$project_dir" branch --show-current 2>/dev/null)" || exit 0
case "$branch" in
  main | master) ;;
  *) exit 0 ;;
esac

input="$(cat)"
target=""
if command -v jq >/dev/null 2>&1; then
  target="$(printf '%s' "$input" |
    jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)"
fi

if [[ "$target" == /* ]]; then
  root="$(git -C "$project_dir" rev-parse --show-toplevel 2>/dev/null)" || exit 0
  case "$target" in
    "$root" | "$root"/*) ;;
    *) exit 0 ;;
  esac
fi

echo "Edits are blocked on the protected branch '$branch'. Run /start-task <slug> to create a feature branch first." >&2
exit 2
