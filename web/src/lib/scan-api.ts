/**
 * Typed browser client for scan-api (POST /scans, GET /scans/{id}) and the findings-hub read API
 * (GET /repos/{owner}/{repo}/scans/{scan_id}).
 *
 * Base URLs come from NEXT_PUBLIC_SCAN_API_URL and NEXT_PUBLIC_HUB_API_URL, which Next.js inlines at
 * build time. They are read as literal `process.env.X` expressions so the inlining works.
 * Report shape: scanner/report.py. Hub shape: hub/findings_hub/api.py.
 */

// ---------- scan-api ----------

export type ScanState = "queued" | "running" | "done" | "error";
export type CheckStatus = "completed" | "partial" | "unavailable" | "error" | "not_applicable";
export type Confidence = "high" | "medium" | "low";

export interface Evidence {
  source_id: string;
  kind: "static" | "telemetry" | "artifact";
  locator: string;
  value: unknown;
  line_start?: number;
  field?: string;
}

export interface ReportCheck {
  check_id: string;
  owner: string;
  adapter: string;
  pattern: string | null;
  layer: string | null;
  category: string | null;
  status: CheckStatus;
  status_source: "scanner" | "detector";
  detector_version: string | null;
  scope_size: number;
  evaluated_size: number;
  finding_count: number;
  reason: string | null;
  limitations: string[];
  limitations_total: number;
  notes?: unknown;
}

export interface ReportFinding {
  id: string;
  check_id: string;
  owner: string;
  layer: string | null;
  pattern: string | null;
  file: string | null;
  line: number | null;
  scope_id: string;
  identity: string;
  summary: string;
  confidence: Confidence;
  recommendation: string;
  references: string[];
  evidence: Evidence[];
  agent_prompt?: string;
}

export interface ScanReport {
  report_version: string;
  scanner: { name: string; version: string };
  scan_id: string;
  scanned_at: string;
  repository: { id: string; url: string; commit_sha: string; commit_source: string };
  files: {
    seen: number;
    collected: number;
    bytes_collected: number;
    skipped: Record<string, number>;
    truncated: boolean;
    by_extension?: Record<string, number>;
  };
  summary: {
    checks_total: number;
    checks_by_status: Record<CheckStatus, number>;
    findings_total: number;
    findings_by_confidence: Record<Confidence, number>;
    findings_by_layer: Record<string, number>;
    findings_by_owner: Record<string, number>;
    taxonomy_checks_total: number;
    adapters_unavailable_or_failed: string[];
  };
  adapters: { owner: string; name: string; status: string; reason: string | null; checks: string[] }[];
  checks: ReportCheck[];
  findings: ReportFinding[];
  impact: { status: string; explanation: string };
  limitations: string[];
}

export interface ScanStatusResponse {
  scan_id: string;
  status: ScanState;
  repo_url: string;
  created_at: string;
  updated_at: string;
  report?: ScanReport | null;
  report_url?: string;
  error?: string;
}

// ---------- hub API ----------

export interface HubResult {
  check_id: string;
  status: CheckStatus;
  commit_sha: string;
  detector_version: string;
  scope_count: number;
  evaluated_count: number;
  finding_count: number;
  limitations: string[];
  evidence: "verified" | "unverified" | string;
  source: string;
  received_at: string;
}

export interface HubFinding {
  check_id: string;
  fingerprint: string;
  scope_id: string;
  identity: string;
  summary: string;
  confidence: Confidence;
  recommendation: string;
  references: string[];
  evidence: Evidence[];
}

export interface HubScan {
  repository_id: string;
  scan_id: string;
  commit_sha: string;
  results: HubResult[];
  findings: HubFinding[];
  findings_total: number;
  truncated: boolean;
}

// ---------- errors & config ----------

export type ApiErrorKind = "unconfigured" | "invalid_input" | "http" | "network" | "bad_response" | "timeout";

export class ApiError extends Error {
  kind: ApiErrorKind;
  status?: number;
  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
  /** Network blips, throttling and 5xx are worth retrying while polling; 4xx are not. */
  get retryable(): boolean {
    return this.kind === "network" || (this.kind === "http" && (this.status === 429 || (this.status ?? 0) >= 500));
  }
}

function baseUrl(value: string | undefined): string | null {
  const trimmed = value?.trim().replace(/\/+$/, "");
  return trimmed ? trimmed : null;
}

export function apiConfig(): { scanApiUrl: string | null; hubApiUrl: string | null } {
  return {
    scanApiUrl: baseUrl(process.env.NEXT_PUBLIC_SCAN_API_URL),
    hubApiUrl: baseUrl(process.env.NEXT_PUBLIC_HUB_API_URL),
  };
}

function requireScanApi(): string {
  const url = apiConfig().scanApiUrl;
  if (!url) throw new ApiError("unconfigured", "NEXT_PUBLIC_SCAN_API_URL is not set, so scans cannot be started.");
  return url;
}

// Same rule as scanner/source.py GITHUB_URL + scan_api/store.py GITHUB_NAME.
const GITHUB_URL = /^https:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/;
const GITHUB_NAME = /^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$/;

/** owner/repo of a public https://github.com/<owner>/<repo> URL, or null. */
export function parseGithubRepo(url: string): { owner: string; repo: string; url: string } | null {
  const match = GITHUB_URL.exec(url.trim());
  if (!match || !GITHUB_NAME.test(match[1]) || !GITHUB_NAME.test(match[2])) return null;
  return { owner: match[1], repo: match[2], url: `https://github.com/${match[1]}/${match[2]}` };
}

// ---------- transport ----------

const REQUEST_TIMEOUT_MS = 30_000;

function withTimeout(signal: AbortSignal | undefined, ms: number): { signal: AbortSignal; done: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new ApiError("network", "request timed out")), ms);
  const onAbort = () => controller.abort(signal?.reason);
  if (signal?.aborted) controller.abort(signal.reason);
  else signal?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    done: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

async function requestJson<T>(url: string, init: RequestInit & { signal?: AbortSignal } = {}): Promise<T> {
  const { signal, done } = withTimeout(init.signal, REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(url, { ...init, signal, cache: "no-store" });
  } catch (error) {
    done();
    if (init.signal?.aborted) throw error; // caller cancelled: propagate the AbortError untouched
    if (error instanceof ApiError) throw error;
    throw new ApiError("network", `Could not reach ${new URL(url).host}: ${(error as Error).message}`);
  }
  try {
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        if (response.ok) throw new ApiError("bad_response", `Expected JSON from ${new URL(url).host}`);
      }
    }
    if (!response.ok) {
      const message = (body as { error?: string } | null)?.error ?? (response.statusText || "request failed");
      throw new ApiError("http", `HTTP ${response.status}: ${message}`, response.status);
    }
    return body as T;
  } finally {
    done();
  }
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

// ---------- scan-api calls ----------

export async function createScan(repoUrl: string, signal?: AbortSignal): Promise<{ scan_id: string; status: "queued" }> {
  const base = requireScanApi();
  const repo = parseGithubRepo(repoUrl);
  if (!repo) throw new ApiError("invalid_input", "Enter a public repository URL like https://github.com/owner/repo.");
  const body = await requestJson<{ scan_id?: string; status?: string }>(`${base}/scans`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_url: repo.url }),
    signal,
  });
  if (!body?.scan_id) throw new ApiError("bad_response", "scan-api accepted the request but returned no scan_id");
  return { scan_id: body.scan_id, status: "queued" };
}

export function getScan(scanId: string, signal?: AbortSignal): Promise<ScanStatusResponse> {
  return requestJson<ScanStatusResponse>(`${requireScanApi()}/scans/${encodeURIComponent(scanId)}`, { signal });
}

export interface PollOptions {
  signal?: AbortSignal;
  onUpdate?: (status: ScanStatusResponse) => void;
  /** Overall budget measured from `startedAt` (default: 15 minutes, the worker limit plus slack). */
  timeoutMs?: number;
  startedAt?: number;
}

export const SCAN_TIMEOUT_MS = 15 * 60_000;

/** Poll GET /scans/{id} with backoff (2 s growing to 15 s) until done/error or the overall timeout. */
export async function pollScan(scanId: string, options: PollOptions = {}): Promise<ScanStatusResponse> {
  const { signal, onUpdate, timeoutMs = SCAN_TIMEOUT_MS, startedAt = Date.now() } = options;
  const deadline = startedAt + timeoutMs;
  let delay = 2_000;
  let failures = 0;
  for (;;) {
    try {
      const status = await getScan(scanId, signal);
      failures = 0;
      onUpdate?.(status);
      if (status.status === "done" || status.status === "error") return status;
    } catch (error) {
      if (signal?.aborted || !(error instanceof ApiError) || !error.retryable || ++failures >= 5) throw error;
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      throw new ApiError("timeout", `No result after ${Math.round(timeoutMs / 60_000)} minutes. The scan may still finish; scan id ${scanId}.`);
    }
    await sleep(Math.min(delay, remaining), signal);
    delay = Math.min(Math.round(delay * 1.5), 15_000);
  }
}

/** The finished report: inline, or downloaded from the presigned report_url for large reports. */
export async function resolveReport(status: ScanStatusResponse, signal?: AbortSignal): Promise<ScanReport> {
  if (status.report) return status.report;
  if (status.report_url) return requestJson<ScanReport>(status.report_url, { signal });
  throw new ApiError("bad_response", "The scan finished but returned neither a report nor a report_url.");
}

// ---------- hub API ----------

/** GET the persisted scan from the findings hub; null on 404 (not persisted yet, or never). */
export async function getHubScan(owner: string, repo: string, scanId: string, signal?: AbortSignal): Promise<HubScan | null> {
  const base = apiConfig().hubApiUrl;
  if (!base) throw new ApiError("unconfigured", "NEXT_PUBLIC_HUB_API_URL is not set.");
  const path = [owner, repo].map(encodeURIComponent).join("/");
  try {
    return await requestJson<HubScan>(`${base}/repos/${path}/scans/${encodeURIComponent(scanId)}`, { signal });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * The hub returns 404 for a few seconds after a scan is done, and its first 200 can hold only part of the
 * results while the rest are still being written. Retry briefly (about 30 s in total) until `isComplete`
 * accepts the response. Transient errors (5xx, 429, network) are retried too: the live API has answered 503
 * once and 200 a moment later. Returns the latest scan seen (null if it never left 404); throws only when
 * every attempt failed or the error is not retryable.
 */
export async function waitForHubScan(
  owner: string, repo: string, scanId: string,
  options: { signal?: AbortSignal; attempts?: number; isComplete?: (scan: HubScan) => boolean } = {},
): Promise<HubScan | null> {
  const { signal, attempts = 7, isComplete = () => true } = options;
  let delay = 2_000;
  let latest: HubScan | null = null;
  let answered = false;
  for (let attempt = 1; ; attempt++) {
    try {
      const scan = await getHubScan(owner, repo, scanId, signal);
      answered = true;
      latest = scan ?? latest;
      if (scan && isComplete(scan)) return scan;
    } catch (error) {
      if (signal?.aborted || !(error instanceof ApiError) || !error.retryable || (attempt >= attempts && !answered)) throw error;
    }
    if (attempt >= attempts) return latest;
    await sleep(delay, signal);
    delay = Math.min(delay * 1.5, 8_000);
  }
}
