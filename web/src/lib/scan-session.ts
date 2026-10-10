/**
 * One Analyze run as a small external store: submit -> poll -> report -> hub, observable with
 * useSyncExternalStore. It is started from the click handler, not from an effect, so React StrictMode's
 * double-mounted effects can never send POST /scans twice.
 */
import { useSyncExternalStore } from "react";
import {
  ApiError,
  apiConfig,
  createScan,
  getHubScan,
  parseGithubRepo,
  pollScan,
  resolveReport,
  waitForHubScan,
  type ApiErrorKind,
  type HubScan,
  type ScanReport,
} from "./scan-api";

export type HubState =
  | { kind: "unconfigured" }
  | { kind: "pending" }
  | { kind: "loading" }
  | { kind: "found"; scan: HubScan }
  | { kind: "missing" }
  | { kind: "error"; message: string };

export type ScanPhase =
  | "unconfigured"
  | "invalid"
  | "submitting"
  | "queued"
  | "running"
  | "loading_report"
  | "done"
  | "failed";

export interface ScanSessionState {
  repoUrl: string;
  phase: ScanPhase;
  startedAt: number;
  finishedAt?: number;
  scanId?: string;
  error?: { kind: ApiErrorKind | "scan_error"; message: string };
  report?: ScanReport;
  hub: HubState;
}

export interface ScanSession {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => ScanSessionState;
  cancel: () => void;
}

/** Phases while the scan is still in flight (the pipeline animation shows); the rest show ScanPanel. */
export const ACTIVE_PHASES: readonly ScanPhase[] = ["submitting", "queued", "running", "loading_report"];

const noSubscription = () => () => {};
const noSnapshot = () => null;

/** Current state of `session`, or null when there is none. */
export function useScanSession(session: ScanSession | null): ScanSessionState | null {
  return useSyncExternalStore(session?.subscribe ?? noSubscription, session?.getSnapshot ?? noSnapshot,
    session?.getSnapshot ?? noSnapshot);
}

function describe(error: unknown): { kind: ApiErrorKind; message: string } {
  if (error instanceof ApiError) return { kind: error.kind, message: error.message };
  return { kind: "network", message: error instanceof Error ? error.message : String(error) };
}

export function startScan(input: string): ScanSession {
  const listeners = new Set<() => void>();
  const controller = new AbortController();
  const { signal } = controller;
  const config = apiConfig();
  const repo = parseGithubRepo(input);
  let state: ScanSessionState = {
    repoUrl: repo?.url ?? input.trim(),
    phase: !config.scanApiUrl ? "unconfigured" : repo ? "submitting" : "invalid",
    startedAt: Date.now(),
    hub: config.hubApiUrl ? { kind: "pending" } : { kind: "unconfigured" },
    error: repo ? undefined : { kind: "invalid_input", message: "Enter a public repository URL like https://github.com/owner/repo." },
  };

  const set = (patch: Partial<ScanSessionState>) => {
    if (signal.aborted) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  const fail = (error: ScanSessionState["error"]) => set({ phase: "failed", error, finishedAt: Date.now() });

  /** `expected`: number of detector-validated checks in the report, i.e. what the hub should end up holding. */
  async function loadHub(owner: string, name: string, scanId: string, retry: boolean, expected = 0) {
    if (!config.hubApiUrl) return null;
    set({ hub: { kind: "loading" } });
    try {
      const scan = retry
        ? await waitForHubScan(owner, name, scanId, { signal, isComplete: (hub) => hub.results.length >= expected })
        : await getHubScan(owner, name, scanId, signal);
      set({ hub: scan ? { kind: "found", scan } : { kind: "missing" } });
      return scan;
    } catch (error) {
      if (!signal.aborted) set({ hub: { kind: "error", message: describe(error).message } });
      return null;
    }
  }

  async function run() {
    if (!repo || !config.scanApiUrl) return;
    let scanId: string;
    try {
      scanId = (await createScan(repo.url, signal)).scan_id;
    } catch (error) {
      if (!signal.aborted) fail(describe(error));
      return;
    }
    set({ scanId, phase: "queued" });

    let final;
    try {
      final = await pollScan(scanId, {
        signal,
        startedAt: state.startedAt,
        onUpdate: (status) => {
          if (status.status === "queued" || status.status === "running") set({ phase: status.status });
        },
      });
    } catch (error) {
      if (!signal.aborted) fail(describe(error));
      return;
    }

    // scan-api normalises the URL; use its owner/repo for the case-sensitive hub lookup.
    const scanned = parseGithubRepo(final.repo_url) ?? repo;
    if (final.status === "error") {
      // The S3 report expires after the retention period; the hub may still hold the results.
      const hub = await loadHub(scanned.owner, scanned.repo, scanId, false);
      if (hub) set({ phase: "done", finishedAt: Date.now(), error: { kind: "scan_error", message: final.error ?? "scan failed" } });
      else fail({ kind: "scan_error", message: final.error ?? "scan failed" });
      return;
    }

    set({ phase: "loading_report" });
    let report: ScanReport | undefined;
    let reportError: ScanSessionState["error"];
    try {
      report = await resolveReport(final, signal);
    } catch (error) {
      if (signal.aborted) return;
      reportError = describe(error);
    }
    set(report ? { phase: "done", report, finishedAt: Date.now() } : { error: reportError });
    const expected = report?.checks.filter((check) => check.status_source === "detector").length ?? 0;
    const hub = await loadHub(scanned.owner, scanned.repo, scanId, true, expected);
    if (!report) {
      if (hub) set({ phase: "done", finishedAt: Date.now() });
      else fail(reportError);
    }
  }

  void run();

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => state,
    cancel: () => controller.abort(),
  };
}
