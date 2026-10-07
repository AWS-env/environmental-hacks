# Decision Log

Architecture Decision Records (ADRs) for this project. One decision per record.
When a decision is made, fill in the **Outcome** and update the index, then close the
linked issue.

## Index

| ID | Decision | Status | Issue |
| --- | --- | --- | --- |
| ADR-000 | Project idea, track, and stack | Proposed | #4 |
| ADR-001 | Repository governance and workflow | Accepted | — |

---

## ADR-000: Project idea, track, and stack

- **Status:** Proposed (not decided)
- **Issue:** #4
- **Date opened:** 2026-10-08

### Context

We are building for WeMakeDevs × AWS **Environmental Hacks** (Bharat Builds Tour, stop 2).
Four days, teams of 1–4, judged on idea & impact, use of AWS, design, execution, and a
3-minute demo. Prizes require at least one AWS open-source tool or an AWS deployment.

We have not yet chosen the problem, the track, or the stack. This ADR records the options
so the decision can be made quickly and written down.

### Decision drivers

- **Demoable in 4 days** — one working feature beats five half-built ones.
- **Real, local impact** — a small problem solved well scores higher.
- **AWS story is visible** — the demo video must show where AWS fits.
- **Data availability** — public, documented, and ideally already on AWS.
- **Team fit** — matches our strongest skills.

### Options — track and problem

| Track | Candidate problems | Data sources |
| --- | --- | --- |
| **Air** | AQI + exposure planner; school "move PE indoors?" alert; stubble-burning warnings | CPCB AQI, OpenAQ, NASA FIRMS, data.gov.in |
| **Heat & Water** | Monsoon commute flooding reporter; housing-society water-tanker predictor; groundwater dashboard | IMD, India WRIS, data.gov.in |
| **Waste & Energy** | Bin-segregation checker (photo → class → score); rooftop-solar payback estimator by pincode | data.gov.in, Registry of Open Data (NASA POWER) |

### Options — stack

| Option | Frontend | Backend | AWS services | Notes |
| --- | --- | --- | --- | --- |
| **A** | React + Vite + TS | Node + TS (Lambda) | API Gateway, Lambda, DynamoDB, S3/CloudFront, Strands Agents | Free-tier friendly, strong AWS surface |
| **B** | Next.js (fullstack) | Next API routes | Amplify, Lambda, DynamoDB, Bedrock | Fastest to wire, fewer AWS services |
| **C** | React + Vite | Python FastAPI | Lambda/App Runner, DynamoDB, S3, Bedrock | Good if we prefer Python for data/ML |

### Evaluation criteria

1. Can we ship a working demo by Sunday?
2. Does it fix a problem we can describe in one sentence?
3. Is the AWS usage meaningful and visible in the video?
4. Is the data reliable and fresh enough to promise in a demo?
5. Does it play to the team's strengths?

### Open questions

- [ ] Which track and problem?
- [ ] Which stack option?
- [ ] Which single AWS service anchors the demo?
- [ ] Who owns frontend / backend / infra / demo video?

### Outcome

> _To be filled when decided. Then update the index above and close #4._

---

## ADR-001: Repository governance and workflow

- **Status:** Accepted
- **Date:** 2026-10-08

### Decision

Trunk-based development on `main`, with an issue-first, one-issue-one-PR workflow,
short-lived branches, rebase-not-merge, and squash-merge only. Enforced by CI (`meta`
and `build`) and branch protection. See `docs/WORKFLOW.md`, `docs/BRANCHING.md`, and
`docs/LABELS.md`.
