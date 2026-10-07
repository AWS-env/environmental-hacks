#!/usr/bin/env bash
# Create a correctly named branch off the latest main.
# Usage: ./scripts/new-branch.sh <issue> <type> <slug>
# Example: ./scripts/new-branch.sh 42 feat aqi-dashboard
set -euo pipefail

issue="${1:-}"
type="${2:-}"
slug="${3:-}"

if [[ -z "$issue" || -z "$type" || -z "$slug" ]]; then
  echo "Usage: $0 <issue> <type> <slug>" >&2
  echo "  type: feat|fix|refactor|docs|test|perf|chore|ci|spike" >&2
  exit 1
fi

case "$type" in
  feat|fix|refactor|docs|test|perf|chore|ci|spike) ;;
  *) echo "Invalid type: $type" >&2; exit 1 ;;
esac

if [[ ! "$issue" =~ ^[0-9]+$ ]]; then
  echo "Issue must be numeric, got: $issue" >&2
  exit 1
fi

branch="${issue}-${type}-${slug}"

git switch main
git pull --rebase origin main
git switch -c "$branch"

echo "Created and switched to $branch"
