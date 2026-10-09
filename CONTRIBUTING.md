# Contributing

Thanks for building with us. This project runs a strict **issue → branch → PR → review → squash-merge** loop so the tree stays clean and conflicts stay rare.

## The rules (read these first)

1. **No direct pushes to `main`.** Everything lands through a pull request.
2. **One issue, one PR.** Every PR must close exactly one issue.
3. **Small PRs win.** Aim for < 400 changed lines. Split big work into stacked, sequential PRs.
4. **Rebase, never merge `main` into your branch.** Keep history linear.
5. **Green before review.** CI must pass and the PR template must be filled in.
6. **Squash-merge only.** Branch is deleted automatically after merge.
7. **Ask before live AWS access.** If an agent needs AWS CLI/MCP context, follow
   [`docs/AWS_AGENT_WORKFLOW.md`](docs/AWS_AGENT_WORKFLOW.md) first.

## 1. Find or open an issue

Work only starts from an issue. Pick the right form:

| If you want to… | Open this issue |
| --- | --- |
| Add new capability | **Feature** |
| Report something broken | **Bug** |
| Change code without changing behaviour | **Refactor** |
| Add/change documentation | **Docs** |
| Tooling, deps, config, chores | **Chore** |
| Time-boxed investigation | **Spike** |

Not sure? Open a Feature and let the maintainers re-label it.

## 2. Branch off `main`

Branch name: `<issue-number>-<type>-<short-slug>`

```bash
git switch main
git pull --rebase origin main
git switch -c 42-feat-aqi-dashboard
```

`type` matches the issue: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `ci`, `spike`.

Use the helper if you like:

```bash
./scripts/new-branch.sh 42 feat aqi-dashboard
```

## 3. Commit

House style (keeps the tree greppable by issue):

```
#<issue> | <type>(<scope>): <summary>
```

Examples:

```
#42 | feat(dashboard): add live AQI card
#57 | fix(api): handle empty sensor payload
#61 | refactor(store): extract fetch hook
```

- `<scope>` is the area: `api`, `web`, `infra`, `data`, `ui`, `auth`, `ci`.
- Imperative, lowercase, no trailing period.
- One logical change per commit; no "wip" commits in the final PR.

## 4. Keep it current

```bash
git fetch origin
git rebase origin/main
# resolve conflicts locally, then:
git push --force-with-lease
```

Never `git merge main`. Never force-push `main`.

## 5. Open the PR

```bash
gh pr create --fill --base main
```

- Title: `#<issue> | <type>(<scope>): <summary>` (same as the commit).
- Body: fill the template. It must contain `Closes #<issue>`.
- Link the issue, add screenshots/demo for UI changes.
- Keep it in **draft** until CI is green.

## 6. Review & merge

- One approving review required (CODEOWNERS are auto-requested).
- All conversations must be resolved.
- CI checks must pass.
- Maintainer **squash-merges**. The branch is auto-deleted.

## What gets a PR rejected

- No linked issue, or more than one issue.
- Direct edit of generated/vendored files.
- Failing CI, merge commits, or an unre-based branch.
- Scope creep: unrelated changes bundled into one PR.

## Commit hygiene tips

- Commit early on your branch; clean up before review.
- If `main` moved and you have conflicts, rebase and re-run tests.
- Keep lockfile changes in their own `chore` PR when possible.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
