/**
 * Where /reports gets its data:
 * - a report finished in this tab is handed over in memory (no second download after "Open full report");
 * - otherwise scan-api `GET /scans/{id}` (report inline, or a presigned report_url for large reports);
 * - when scan-api no longer has it (reports expire after 7 days) or the scan failed, the findings-hub copy;
 * - scans seen in this browser are listed in localStorage, with finding fingerprints for commit comparison.
 */
import { useSyncExternalStore } from "react";
import {
  ApiError,
  apiConfig,
  getHubScan,
  getScan,
  parseGithubRepo,
  pollScan,
  resolveReport,
  type CheckStatus,
  type Confidence,
  type ScanReport,
  type ScanState,
} from "./scan-api";
import { fromHub, fromReport, totals, type ReportModel } from "./report-model";

// ---------- in-memory handover ----------

const reports = new Map<string, ScanReport>();

/** Keep a finished report for this page session, so /reports opens it without downloading it again. */
export function rememberReport(report: ScanReport) {
  reports.set(report.scan_id, report);
}

// ---------- scan history (localStorage) ----------

const HISTORY_KEY = "ecoaudit.scan-history.v1";
const HISTORY_EVENT = "ecoaudit:scan-history";
const MAX_ENTRIES = 12;
/** Fingerprint prefix length: 16 hex chars (64 bits) is ample to tell findings of one repository apart. */
const PRINT_CHARS = 16;
const MAX_PRINTS = 5000;

export interface ScanHistoryEntry {
  scanId: string;
  repoId: string;
  repoUrl: string | null;
  commitSha: string;
  scannedAt: string | null;
  recordedAt: string;
  scannerVersion: string | null;
  /** "hub" when only the findings-hub copy was available (detector-validated checks only). */
  source: "scan" | "hub";
  findings: number;
  byConfidence: Record<Confidence, number>;
  checks: number;
  byStatus: Record<CheckStatus, number>;
  fingerprints: string[];
  /** False when the list was capped (or the hub list was truncated), so a comparison would be incomplete. */
  fingerprintsComplete: boolean;
}

let cachedRaw: string | null | undefined;
let cachedEntries: ScanHistoryEntry[] = [];
const EMPTY: ScanHistoryEntry[] = [];

function isEntry(e: unknown): e is ScanHistoryEntry {
  const x = e as ScanHistoryEntry;
  return Boolean(x && typeof x.scanId === "string" && typeof x.repoId === "string" && typeof x.commitSha === "string"
    && Array.isArray(x.fingerprints) && x.byConfidence && x.byStatus);
}

const time = (e: ScanHistoryEntry) => Date.parse(e.scannedAt ?? e.recordedAt) || 0;

function readHistory(): ScanHistoryEntry[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(HISTORY_KEY);
  } catch {
    return EMPTY; // storage disabled (private mode, policy): history is simply empty
  }
  if (raw === cachedRaw) return cachedEntries;
  cachedRaw = raw;
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    cachedEntries = Array.isArray(parsed) ? parsed.filter(isEntry).sort((a, b) => time(b) - time(a)) : [];
  } catch {
    cachedEntries = [];
  }
  return cachedEntries;
}

function writeHistory(entries: ScanHistoryEntry[]) {
  // Newest first; on quota errors drop the oldest scans until it fits.
  for (let keep = Math.min(entries.length, MAX_ENTRIES); keep >= 0; keep--) {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, keep)));
      break;
    } catch {
      if (keep === 0) return;
    }
  }
  window.dispatchEvent(new Event(HISTORY_EVENT));
}

/** Record a scan this browser has seen. Samples and uploaded files are not scans of ours and are never recorded. */
export function recordScan(model: ReportModel) {
  if (typeof window === "undefined" || (model.origin !== "scan" && model.origin !== "hub")) return;
  const t = totals(model);
  const prints = model.findings.map((f) => f.id.slice(0, PRINT_CHARS));
  const entry: ScanHistoryEntry = {
    scanId: model.scanId,
    repoId: model.repository.id,
    repoUrl: model.repository.url,
    commitSha: model.repository.commitSha,
    scannedAt: model.scannedAt,
    recordedAt: new Date().toISOString(),
    scannerVersion: model.scanner?.version ?? null,
    source: model.origin,
    findings: t.findings,
    byConfidence: t.byConfidence,
    checks: t.checks,
    byStatus: t.byStatus,
    fingerprints: prints.slice(0, MAX_PRINTS),
    fingerprintsComplete: prints.length <= MAX_PRINTS && !model.hub?.truncated,
  };
  const history = readHistory();
  // A full report beats a hub copy of the same scan; never downgrade an entry.
  if (entry.source === "hub" && history.some((e) => e.scanId === entry.scanId && e.source === "scan")) return;
  writeHistory([entry, ...history.filter((e) => e.scanId !== entry.scanId)].sort((a, b) => time(b) - time(a)));
}

export function forgetScan(scanId: string) {
  writeHistory(readHistory().filter((e) => e.scanId !== scanId));
}

function subscribe(listener: () => void) {
  const onStorage = (event: StorageEvent) => { if (event.key === null || event.key === HISTORY_KEY) listener(); };
  window.addEventListener("storage", onStorage);
  window.addEventListener(HISTORY_EVENT, listener);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(HISTORY_EVENT, listener);
  };
}

/** Scans seen in this browser, newest first. Empty during server rendering. */
export function useScanHistory(): ScanHistoryEntry[] {
  return useSyncExternalStore(subscribe, readHistory, () => EMPTY);
}

export function latestScan(): ScanHistoryEntry | null {
  return typeof window === "undefined" ? null : readHistory()[0] ?? null;
}

export interface Comparison {
  previous: ScanHistoryEntry;
  added: string[];
  resolvedCount: number;
  unchanged: number;
  complete: boolean;
}

/** Compare a report with the previous scan of the same repository seen in this browser. */
export function compareWithPrevious(model: ReportModel, history: ScanHistoryEntry[]): Comparison | null {
  const current = time({ scannedAt: model.scannedAt, recordedAt: new Date().toISOString() } as ScanHistoryEntry);
  const previous = history.find((e) => e.repoId === model.repository.id && e.scanId !== model.scanId && time(e) < current);
  if (!previous) return null;
  const before = new Set(previous.fingerprints);
  const now = new Set(model.findings.map((f) => f.id.slice(0, PRINT_CHARS)));
  const added = model.findings.filter((f) => !before.has(f.id.slice(0, PRINT_CHARS))).map((f) => f.id);
  let resolvedCount = 0;
  for (const print of before) if (!now.has(print)) resolvedCount += 1;
  return {
    previous,
    added,
    resolvedCount,
    unchanged: now.size - added.length,
    complete: previous.fingerprintsComplete && !model.hub?.truncated && model.findings.length <= MAX_PRINTS,
  };
}

// ---------- loading ----------

export type LoadProgress =
  | { kind: "fetching" }
  | { kind: "running"; status: Extract<ScanState, "queued" | "running">; repoUrl: string }
  | { kind: "downloading" }
  | { kind: "hub" };

export class ReportLoadError extends Error {
  title: string;
  constructor(title: string, message: string) {
    super(message);
    this.name = "ReportLoadError";
    this.title = title;
  }
}

async function hubFallback(repoUrl: string | null, scanId: string, scannedAt: string | null, reason: string, signal: AbortSignal) {
  const repo = repoUrl ? parseGithubRepo(repoUrl) : null;
  if (!repo || !apiConfig().hubApiUrl) return null;
  try {
    const hub = await getHubScan(repo.owner, repo.repo, scanId, signal);
    return hub ? fromHub(hub, scannedAt, reason) : null;
  } catch (error) {
    if (signal.aborted) throw error;
    return null;
  }
}

/**
 * The report for `scanId`: memory, then scan-api (waiting for a scan that is still running), then the hub.
 * Throws ReportLoadError with a user-facing explanation when no source has it.
 */
export async function loadScanReport(scanId: string, signal: AbortSignal, onProgress: (p: LoadProgress) => void): Promise<ReportModel> {
  const cached = reports.get(scanId);
  if (cached) return fromReport(cached, "scan");
  const known = readHistory().find((e) => e.scanId === scanId) ?? null;

  if (!apiConfig().scanApiUrl) {
    throw new ReportLoadError("Scanning is not configured",
      "NEXT_PUBLIC_SCAN_API_URL was not set when this site was built, so stored scans cannot be fetched.");
  }

  onProgress({ kind: "fetching" });
  let status;
  try {
    status = await getScan(scanId, signal);
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof ApiError && error.status === 404) {
      onProgress({ kind: "hub" });
      const hub = await hubFallback(known?.repoUrl ?? null, scanId, known?.scannedAt ?? null,
        "scan-api no longer has this scan (results are kept for 7 days)", signal);
      if (hub) return hub;
      throw new ReportLoadError("This scan is no longer available",
        "scan-api keeps scan results for 7 days and this one is not there anymore"
        + (known ? ", and the findings hub has no copy of it." : ". Run a new scan to get a fresh report."));
    }
    throw new ReportLoadError("Could not reach scan-api", error instanceof Error ? error.message : String(error));
  }

  if (status.status === "queued" || status.status === "running") {
    onProgress({ kind: "running", status: status.status, repoUrl: status.repo_url });
    try {
      status = await pollScan(scanId, {
        signal,
        // Budget from when this page started waiting: created_at is server time and may be long past.
        startedAt: Date.now(),
        onUpdate: (s) => {
          if (s.status === "queued" || s.status === "running") onProgress({ kind: "running", status: s.status, repoUrl: s.repo_url });
        },
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new ReportLoadError("The scan did not finish", error instanceof Error ? error.message : String(error));
    }
  }

  if (status.status === "done") {
    try {
      if (!status.report) onProgress({ kind: "downloading" });
      const report = await resolveReport(status, signal);
      rememberReport(report);
      return fromReport(report, "scan");
    } catch (error) {
      if (signal.aborted) throw error;
      onProgress({ kind: "hub" });
      const message = error instanceof Error ? error.message : String(error);
      const hub = await hubFallback(status.repo_url, scanId, null, `the report could not be downloaded (${message})`, signal);
      if (hub) return hub;
      throw new ReportLoadError("The report could not be downloaded", error instanceof Error ? error.message : String(error));
    }
  }

  // The scan failed; the hub may still hold the checks that completed before the failure.
  onProgress({ kind: "hub" });
  const hub = await hubFallback(status.repo_url, scanId, null, `scan-api reported: ${status.error ?? "scan failed"}`, signal);
  if (hub) return hub;
  throw new ReportLoadError("The scan failed", status.error ?? "scan failed");
}

export const SAMPLE_REPORT_URL = "/sample-report.json";
