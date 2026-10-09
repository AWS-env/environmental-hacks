# Environmental Hacks

Build what the planet needs — our entry for the **WeMakeDevs × AWS Environmental Hacks** hackathon (Bharat Builds Tour, stop 2).

> Stack and project idea are still being decided. This repo currently holds the **governance, workflow, and CI scaffolding** so the tree stays clean from the first commit.

## Tracks (pick one)

| Track | Problems |
| --- | --- |
| **Air** | AQI, pollution exposure, stubble burning, indoor air, school safety |
| **Heat & Water** | Heatwaves, floods, monsoon waterlogging, droughts, water tankers, leaks, groundwater |
| **Waste & Energy** | Segregation, recycling, e-waste, informal recyclers, rooftop solar, EV nudges, public transport |

To be eligible for prizes the project must use **at least one AWS open-source tool or be deployed on AWS**.

## How we work

Issue first → branch → PR → review → squash-merge. One issue, one PR. Small, rebased, green.

```bash
# 1. Pick an issue, then branch off main
git switch main && git pull --rebase
git switch -c 42-feat-aqi-dashboard

# 2. Commit with the house style
git commit -m "#42 | feat(dashboard): add live AQI card"

# 3. Keep it current, then push
git fetch origin && git rebase origin/main
git push -u origin 42-feat-aqi-dashboard

# 4. Open the PR (Closes #42), get a review, squash-merge
gh pr create --fill --base main
```

See [`docs/WORKFLOW.md`](docs/WORKFLOW.md) for the full loop and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the rules.

Detector owners: start with the [shared detector contract](docs/DETECTOR_CONTRACT.md)
and [per-issue verification plan](docs/VERIFICATION_PLAN.md). They define common
inputs, evidence-backed outputs, measurement provenance, and scan comparison.

## Scan a repository

`scanner/` runs every detector over one repository and writes a single `report.json`. It
reads files as text only and never executes, installs, builds or imports the scanned code.

```bash
python3 -m venv .venv && .venv/bin/pip install -r shared/contracts/requirements.txt
npm ci                                                                      # owner B (php-parser)
npm --prefix detectors/owner-a ci && npm --prefix detectors/owner-a run build  # owner A (tree-sitter)

.venv/bin/python -m scanner scan https://github.com/aws/aws-sam-cli -o report.json
.venv/bin/python -m scanner scan path/to/local/repo -o report.json --owners C,D
```

- **Input:** a public `https://github.com/<owner>/<repo>` URL (shallow `git clone --depth 1` into a
  temporary directory, deleted afterwards) or a local directory. The report records the full commit SHA.
- **Collection limits:** skips `.git`, vendored/build directories (`node_modules`, `vendor`, `venv`,
  `dist`, ...), symlinks, lock files, binaries and files over 1 MB, and stops at 5,000 files. Every skip
  is counted in `report.files`.
- **Detectors:** owner A (Python CODE-C\* checks, via Node), owner B (DB-34 for PHP, via Node),
  owner C (PY-\* checks) and owner D (every module in `owner_d`). Every result passes
  `shared.contracts.validation.validate_pair` before it is reported.
- **Statuses:** `completed`/`partial`/`unavailable`/`error` come from the detector contract.
  `not_applicable` means the repository has no files that the check examines. A crash or an invalid
  result becomes `error`. A check that needs telemetry or profiler artifacts stays `unavailable`.
  None of these count as a pass.
- **report.json:** repository and commit, file counts, adapter status, one entry per check (status,
  coverage, limitations), findings (exact evidence lines, confidence, recommendation, references and a
  copyable `agent_prompt` for your coding agent) and summary counts.

**Limitations:** the analysis is static, so a finding proves the pattern at the cited line, not what
it costs at runtime. Impact is reported as `not_quantified`, and no energy, CO2 or water figures are
estimated (`report.impact` holds a placeholder for a later SCI-based estimate engine). Only the checks
implemented in this repository run, which is a small share of the taxonomy. Owner A and owner C skip
test files. Owner B runs through its pre-contract `scanSource` entry point, translated by the scanner.
If Node.js or the owner A build is missing, that owner is reported as `unavailable`.

Open decisions (idea, track, stack) live in [`docs/DECISIONS.md`](docs/DECISIONS.md).

For agent-assisted AWS work, use the shared handshake in [`docs/AWS_AGENT_WORKFLOW.md`](docs/AWS_AGENT_WORKFLOW.md): agents ask before using AWS CLI/MCP, then verify local profile identity and selected Region before inspecting service state.

## Repo layout

```
.github/            issue forms, PR template, CI, CODEOWNERS, dependabot
docs/               workflow, branching, and label docs
detectors/          per-owner detectors (contract v1)
scanner/            repository scan runner -> report.json
scripts/            helper scripts (branch + label setup)
```

## Quick links

- Hackathon: https://www.wemakedevs.org/aws/env
- Rules: https://www.wemakedevs.org/aws/env/rules
- Discord: https://discord.gg/wemakedevs
- AWS Builder Center: https://builder.aws.com

## License

[MIT](LICENSE)
