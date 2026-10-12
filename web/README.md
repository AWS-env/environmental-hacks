This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Configuration

The Analyze flow calls two live APIs from the browser. Both URLs are read from
`NEXT_PUBLIC_*` variables, which Next.js inlines **at build time**, so set them
before `npm run build` (Amplify: App settings > Environment variables) or in
`.env.local` for `npm run dev`. Copy [`.env.example`](.env.example) to start.

| Variable | Used for | When unset |
| --- | --- | --- |
| `NEXT_PUBLIC_SCAN_API_URL` | scan-api ([`infra/scan-api`](../infra/scan-api/README.md)): `POST /scans`, `GET /scans/{scan_id}` | Analyze shows "Scanning is not configured". Nothing is called and no sample data is shown. |
| `NEXT_PUBLIC_HUB_API_URL` | findings-hub read API ([`hub/README.md`](../hub/README.md)): `GET /repos/{owner}/{repo}/scans/{scan_id}` | Results come from the scan report only, and the view says the hub is not configured. |

Use the base URL with no trailing path, e.g. `https://<api-id>.execute-api.ap-south-1.amazonaws.com`.
Both APIs allow CORS from `*` today.

## Analyze flow

`src/app/home/page.tsx` starts a scan session (`src/lib/scan-session.ts`) when Analyze is clicked and
swaps the hero for `src/components/scan/ScanPanel.tsx`:

1. `POST /scans` with the normalised `https://github.com/<owner>/<repo>` URL (public repositories only).
2. Poll `GET /scans/{scan_id}` with backoff (2 s, growing to 15 s) and give up after 15 minutes.
   Transient 5xx, 429 and network errors are retried.
3. Load the report: inline `report`, or the presigned `report_url` for large reports.
4. Read the findings hub. It returns 404 for a few seconds after a scan finishes, and its first answer can
   be partial, so it is retried for about 30 s. If the S3 report has expired, the hub copy is shown instead.

The results show every check's status (completed, partial, error, unavailable, not applicable) and each
finding with its `file:line`, the cited source lines, the recommendation and the references. The typed
client is `src/lib/scan-api.ts`. Report fields follow `scanner/report.py`, and hub fields follow
`hub/findings_hub/api.py`.

## Report page

`/reports` (`src/components/reports/`, in the workspace shell with the sidebar) shows one scan as a full report,
in five tabs: Overview, Findings (list and detail), Checks, Provenance and History (scans seen in this browser). "Open full report" on a finished scan links to it.
It picks the report from the URL:

| URL | Report |
| --- | --- |
| `/reports?scan=<scan_id>` | That scan. A report finished in this tab is reused from memory. Otherwise it comes from `GET /scans/{scan_id}`, and the page waits while the scan is still running. If scan-api no longer has it (reports expire after 7 days) or the scan failed, the findings-hub copy is shown, labelled as such. |
| `/reports` | The latest scan in this browser's history, or the sample when there is none. |
| `/reports?sample=1` | `public/sample-report.json`, a real scan of `fastapi/full-stack-fastapi-template` labelled as a sample. |

"Open report.json" opens a CLI report from disk (`python -m scanner scan ... -o report.json`). It is read
in the browser and never uploaded. "Export report.json" downloads the report being viewed.

The history (`src/lib/report-store.ts`) lives in `localStorage` and holds at most 12 scans. It stores
each scan's counts and 16-character finding-fingerprint prefixes, so a scan can be compared with the
previous scan of the same repository. Fingerprints exclude line numbers and commits, so a finding that
moved lines is still the same finding.

Nothing on the page is estimated. The scanner reports environmental impact as `not_quantified`, and the
page shows that with the planned SCI methodology instead of numbers.

To refresh the sample, run from the repository root with every owner's detectors installed:

```bash
python3 -m scanner scan https://github.com/fastapi/full-stack-fastapi-template -o /tmp/sample.json
python3 -c "import json; r = json.load(open('/tmp/sample.json')); open('web/public/sample-report.json', 'w').write(json.dumps(r, separators=(',', ':'), ensure_ascii=False) + '\n')"
```

Check that the summary has no `error` checks and no `adapters_unavailable_or_failed`. Those mean
a detector dependency is missing locally.

## Environment page

`/environment` (`src/components/environment/`) shows run-it's live footprint for the machine it runs on:
- measured power over time,
- energy, with CO₂e and water modelled from cited factors,
- where the command's energy went,
- the process table,
- run-it's folder scan.

It polls `http://127.0.0.1:<port>/state` (`src/lib/run-it.ts`, port 47321 or `?port=`) at run-it's own
sampling interval, and looks for run-it every 3 s otherwise. When run-it stops, the page keeps its last reading.
When run-it was never found, the page shows `public/run-it-sample.json`: a real `run-it npm run build` session
recorded on a Mac. The sample contains only that command's own processes. See [`run_it/README.md`](../run_it/README.md).

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Audit overview

`/overview` presents illustrative repository findings, taxonomy distribution,
scan duration and evidence coverage. Its generated workspace data is isolated in
`src/lib/overview-model.ts`; it does not read the device or call AWS, and is never
used as a real scan result. Candidates stay separate from findings; missing
runtime evidence stays unavailable. Energy and CO2e remain unquantified until
workload, infrastructure, region and documented model inputs are available.
A single footer identifies illustrative figures. Pause updates, switch repository,
change the history window, or toggle chart series. GSAP animates counters and
chart paths. The shared route transition uses the original drifting particles, types the
destination route and scatters on arrival, respecting reduced motion.

## Illustrative particle pipeline

The loading preview uses the shared architecture graph, with seven independent
particle streams for dashboard delivery, static scans, artifacts, telemetry,
estimates/readback, OAuth and scheduled alerts. Trails and stage highlights are
visual illustrations, not live AWS telemetry. The status bar still reports the
real scan API state and elapsed time; completed/error states use the real report
flow. Client code is never executed by this preview.
