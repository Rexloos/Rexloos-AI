#!/usr/bin/env bash
# Commit reviewed repository changes locally. This script never invokes git push.
set -euo pipefail

if [[ $# -ne 1 || -z "${1//[[:space:]]/}" || "$1" == *$'\n'* || "$1" == *$'\r'* ]]; then
    echo 'Usage: bash scripts/auto_commit.sh "English commit title"' >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(git -C "$script_dir" rev-parse --show-toplevel)
cd -- "$repo_dir"

if [[ -n "$(git ls-files --unmerged)" ]]; then
    echo 'Cannot auto-commit: resolve merge conflicts first.' >&2
    exit 1
fi

if [[ -z "$(git status --porcelain --untracked-files=all)" ]]; then
    echo 'No changes to commit.'
    exit 0
fi

git add --all -- .
if git diff --cached --quiet; then
    echo 'No staged changes to commit.'
    exit 0
fi

git commit -m "$1"
git log -1 --format='Created local commit: %h %s'
