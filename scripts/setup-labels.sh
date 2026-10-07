#!/usr/bin/env bash
# Create/update the label taxonomy on the current repo.
# Requires: gh (authenticated). Run from the repo root.
# Usage: ./scripts/setup-labels.sh [owner/repo]
set -euo pipefail

repo="${1:-}"
gh_args=()
[[ -n "$repo" ]] && gh_args=(--repo "$repo")

label() {
  local name="$1" color="$2" desc="$3"
  gh label create "$name" --color "$color" --description "$desc" --force "${gh_args[@]}"
  echo "  $name"
}

echo "Type labels:"
label "type: feature"  "1d76db" "New capability"
label "type: bug"      "d73a4a" "Something broken"
label "type: refactor" "5319e7" "No behaviour change"
label "type: docs"     "0075ca" "Documentation"
label "type: chore"    "cfd3d7" "Tooling, deps, config"
label "type: test"     "fbca04" "Tests only"
label "type: perf"     "0e8a16" "Performance"
label "type: ci"       "bfd4f2" "CI/build"
label "type: spike"    "c5def5" "Investigation, no production code"

echo "Status labels:"
label "status: triage"      "ededed" "Needs triage"
label "status: ready"       "0e8a16" "Scoped, ready to pick up"
label "status: in-progress" "fbca04" "Someone is on it"
label "status: blocked"     "b60205" "Waiting on something"
label "status: review"      "1d76db" "PR open, awaiting review"

echo "Priority labels:"
label "priority: p0" "b60205" "Critical, drop everything"
label "priority: p1" "d93f0b" "High"
label "priority: p2" "fbca04" "Medium"
label "priority: p3" "0e8a16" "Low"

echo "Area labels:"
label "area: frontend" "c2e0c6" "UI/web"
label "area: backend"  "bfdadc" "API/server"
label "area: infra/aws" "fef2c0" "AWS, IaC, deploy"
label "area: data"     "d4c5f9" "Data, ETL, SQL"
label "area: ui/ux"    "f9d0c4" "Design"
label "area: docs"     "0075ca" "Documentation"
label "area: ci"       "bfd4f2" "Workflows, scripts"

echo "Size labels:"
label "size: xs" "ededed" "Under 50 changed lines"
label "size: s"  "d4c5f9" "Under 200 changed lines"
label "size: m"  "fbca04" "Under 400 changed lines"
label "size: l"  "d93f0b" "Under 800 changed lines"
label "size: xl" "b60205" "Over 800 lines — split it"

echo "Special labels:"
label "breaking change" "b60205" "Breaking API or behaviour change"
label "do not merge"    "000000" "Blocked from merging"

echo "Done."
