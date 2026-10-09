<!--
PR title must be: #<issue> | <type>(<scope>): <summary>
Fill every section. Draft PRs are welcome; keep it in draft until CI is green.
-->

## What & why

<!-- One or two sentences. Link the issue: -->

Closes #

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `refactor` — no behaviour change
- [ ] `docs` — documentation
- [ ] `test` — tests only
- [ ] `perf` — performance
- [ ] `chore` — tooling/deps/config
- [ ] `ci` — CI/build
- [ ] `spike` — investigation

## How I tested

<!-- Commands run, screenshots, or a short demo. Reviewers should be able to reproduce. -->

<!-- Detector changes: link the issue's verification-plan comment and map case IDs
to committed fixtures/tests. Follow docs/DETECTOR_CONTRACT.md and
docs/VERIFICATION_PLAN.md. State missing evidence and unsupported inputs. -->

## Checklist

- [ ] Exactly one issue linked (`Closes #`)
- [ ] Branch rebased on latest `main` (no merge commits)
- [ ] Commit message follows `#<issue> | <type>(<scope>): <summary>`
- [ ] CI is green
- [ ] Tests added/updated where behaviour changed
- [ ] Detector changes validate input/result pairs and cover positive, negative, and missing-evidence cases (if applicable)
- [ ] Docs updated (if user-facing)
- [ ] AWS CLI/MCP access followed `docs/AWS_AGENT_WORKFLOW.md` (if applicable)
- [ ] No secrets, credentials, or large binaries committed
- [ ] Scope kept small (< ~400 changed lines)

## Screenshots / demo (UI changes)

<!-- Before/after or a short clip. Delete if not applicable. -->

## Notes for reviewers

<!-- Anything tricky, deliberate trade-offs, follow-ups. -->
