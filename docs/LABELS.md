# Labels

The label taxonomy. Grouped by prefix so they sort together.

## Type (what kind of work)

| Label | Use |
| --- | --- |
| `type: feature` | New capability |
| `type: bug` | Something broken |
| `type: refactor` | No behaviour change |
| `type: docs` | Documentation |
| `type: chore` | Tooling, deps, config |
| `type: test` | Tests only |
| `type: perf` | Performance |
| `type: ci` | CI/build |
| `type: spike` | Investigation, no production code |

## Status (where it is)

| Label | Use |
| --- | --- |
| `status: triage` | New, needs triage |
| `status: ready` | Scoped, ready to pick up |
| `status: in-progress` | Someone is on it |
| `status: blocked` | Waiting on something |
| `status: review` | PR open, awaiting review |

## Priority

| Label | Use |
| --- | --- |
| `priority: p0` | Critical, drop everything |
| `priority: p1` | High |
| `priority: p2` | Medium |
| `priority: p3` | Low |

## Area

| Label | Use |
| --- | --- |
| `area: frontend` | UI/web |
| `area: backend` | API/server |
| `area: infra/aws` | AWS, IaC, deploy |
| `area: data` | Data, ETL, SQL |
| `area: ui/ux` | Design |
| `area: docs` | Docs |
| `area: ci` | Workflows, scripts |

## Size (on PRs)

`size: xs` (<50) · `size: s` (<200) · `size: m` (<400) · `size: l` (<800) · `size: xl` (>800, split it)

## Special

`good first issue` · `help wanted` · `dependencies` · `do not merge` · `breaking change`

## Conventions

- Every issue gets **one type**, **one status**, and at least **one area**.
- Every PR inherits the type from its issue.
- Maintainers own `priority` and `status`.
