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

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
