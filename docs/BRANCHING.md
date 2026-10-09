# Branching

Trunk-based. `main` is the trunk and the only long-lived branch.

## Rules

- `main` is always deployable. No direct pushes; PRs only.
- Branches are **short-lived** (hours to a couple of days).
- Branch off the latest `main`; delete after merge (automatic).
- Never `git merge main` into a feature branch — rebase.

## Naming

```
[owner-x/]<issue-number>-<type>-<short-slug>
```

- `owner-x`: `owner-a` | `owner-b` | `owner-c` | `owner-d` — **required for taxonomy-check
  work** (optional otherwise). Keeps each owner's branches visually separated.
- `type`: `feat` | `fix` | `refactor` | `docs` | `test` | `perf` | `chore` | `ci` | `spike`
- `slug`: lowercase, hyphens, 2–5 words

Examples:

```
owner-a/123-feat-code-c1-1-detector
owner-b/140-feat-db-n-plus-one
57-fix-empty-sensor-payload
61-refactor-extract-fetch-hook
```

## Creating one

```bash
git switch main && git pull --rebase origin main
git switch -c 42-feat-aqi-dashboard
# or
./scripts/new-branch.sh 42 feat aqi-dashboard
```

## Keeping current

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

## Long-running work

Do not keep a giant branch open for days. Split it:

1. Land the scaffold/interface behind a feature flag.
2. Land increments as separate issues/PRs.
3. Remove the flag in a final `chore` PR.

This keeps every PR small and reviewable, and avoids a merge nightmare at the end.

## Protected branches

`main` is protected: PR required, 1 approval, status checks required, linear history,
no force-push, no deletion, conversation resolution required.
