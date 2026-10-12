# Lighthouse worker

Headless Chromium + Lighthouse audit microservice. `POST /audit` runs a Lighthouse audit for a URL, saves the
**full JSON and HTML report to a private S3 bucket**, and returns the scores plus presigned preview links.

## API

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health, concurrency and queue metrics |
| `POST` | `/audit` | Runs an audit. Body: `{ "url": "https://...", "owner": "optional-folder-name" }` |

`/audit` response (extra block when S3 is configured):

```json
{
  "url": "https://example.com",
  "scores": { "performance": 100, "accessibility": 96, "best_practices": 100, "seo": 80 },
  "metrics": { "...": "..." },
  "load_success": true,
  "reports": {
    "json_s3_key": "lighthouse/<owner>/<timestamp>-<id>.json",
    "html_s3_key": "lighthouse/<owner>/<timestamp>-<id>.html",
    "json_preview_url": "https://...presigned...",
    "html_preview_url": "https://...presigned...",
    "report_url_expires_in_sec": 604800
  }
}
```

- `owner` defaults to `shared` and is sanitized to `[a-zA-Z0-9_-]`.
- If S3 is not configured, `reports` is omitted. If the upload fails, the audit still returns and
  `reports.report_error` carries the message.
- The bucket is private. Opening a plain object URL returns 403; the presigned `*_preview_url` opens the
  rendered HTML report in a browser until it expires.

## Configuration (environment variables)

| Variable | Purpose |
| :--- | :--- |
| `LIGHTHOUSE_REPORTS_BUCKET` | Bucket to save reports to. Unset = no S3 saving |
| `AWS_REGION` | Bucket Region (default `ap-south-1`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Write-only credential for the `lighthouse/` prefix |
| `REPORT_URL_TTL_SEC` | Presigned link lifetime (default 604800 = 7 days; max 7 days for IAM user keys) |
| `API_KEY` | If set, `/audit` requires `x-api-key` or `Authorization: Bearer` |
| `MAX_CONCURRENT_AUDITS`, `AUDIT_TIMEOUT_MS`, `PORT`, `CHROME_PATH` | Tuning |

Keep credentials in an env file on the host (mode 600, e.g. `.env.aws`, git-ignored here). Never commit them.

## Build and run

```bash
docker build -t lighthouse-worker:1.2 .
docker run -d --name lighthouse-worker --restart unless-stopped \
  -p 127.0.0.1:3000:3000 --env-file .env.aws lighthouse-worker:1.2
curl -s http://127.0.0.1:3000/health
```

The IAM identity needs only `s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::<bucket>/lighthouse/*`.

## Live demo helper

`scripts/lighthouse-demo.ps1 -Url https://example.com` opens a temporary SSH tunnel to the worker, runs a live
audit and opens the HTML report in Chrome. Set `LIGHTHOUSE_SSH_HOST` (`user@host`) first.
