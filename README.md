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

## Repo layout

```
.github/            issue forms, PR template, CI, CODEOWNERS, dependabot
docs/               workflow, branching, and label docs
scripts/            helper scripts (branch + label setup)
```

## Quick links

- Hackathon: https://www.wemakedevs.org/aws/env
- Rules: https://www.wemakedevs.org/aws/env/rules
- Discord: https://discord.gg/wemakedevs
- AWS Builder Center: https://builder.aws.com

## License

[MIT](LICENSE)
