# Workflow

The full loop, from idea to merged code. Designed so conflicts stay rare and `main` is always shippable.

## The loop

```
issue ──▶ branch ──▶ commits ──▶ rebase ──▶ PR ──▶ review ──▶ squash-merge ──▶ main
  ▲                                                                              │
  └──────────────────────── follow-up issue if needed ◀─────────────────────────┘
```

## 1. Issue first

Nothing is built without an issue. Use the correct form (Feature, Bug, Refactor, Docs, Chore, Spike). A maintainer triages it: sets `status: ready`, priority, and area.

An issue is "ready" when it has: a clear problem, acceptance criteria, and an area label.

## 2. Branch

Always branch from the latest `main`:

```bash
git switch main
git pull --rebase origin main
git switch -c 42-feat-aqi-dashboard
```

Naming: `<issue>-<type>-<slug>`.

## 3. Commit

House style, grep-able by issue:

```
#<issue> | <type>(<scope>): <summary>
```

One logical change per commit. Keep the diff small.

## 4. Rebase before review

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

Never merge `main` into the branch — CI rejects merge commits.

## 5. Pull request

```bash
gh pr create --fill --base main
```

The PR must:
- have a title matching `#<issue> | <type>(<scope>): <summary>`
- link exactly one issue (`Closes #42`)
- fill the checklist
- pass CI (`meta` + `build`)

## 6. Review

- CODEOWNERS are auto-requested.
- One approval required; conversations must be resolved.
- Reviewer checks: correctness, scope, tests, naming, no secrets.

## 7. Merge

Maintainer **squash-merges** with a conventional title. The head branch is auto-deleted. `main` must stay linear.

## Why conflicts stay minimal

| Practice | Effect |
| --- | --- |
| Short-lived branches | Less time for `main` to drift |
| Small PRs | Fewer overlapping lines |
| Rebase, not merge | Linear history, no merge-commit tangles |
| One issue per PR | Non-overlapping scopes |
| Area labels + CODEOWNERS | Right reviewer, earlier |
| `git pull --rebase` habit | Local branches stay current |

## When you do hit a conflict

```bash
git fetch origin
git rebase origin/main
# fix each file, then:
git add <file>
git rebase --continue
git push --force-with-lease
```

If the conflict is in a file another open PR also touches, coordinate on Discord before rebasing to avoid ping-pong.

## Hotfixes

Same loop. If `main` is broken, open a `fix` issue, branch `NN-fix-<slug>`, and PR with `priority: p0`. Squash-merge once CI is green.
