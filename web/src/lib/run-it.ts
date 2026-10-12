/**
 * Live link to run-it (run_it/live.py) on this machine: GET http://127.0.0.1:<port>/state, schema "run-it.state/1".
 * While run-it is not running, /environment shows a recorded session (public/run-it-sample.json) instead.
 */
import { useEffect, useRef, useState } from "react";

export const DEFAULT_PORT = 47321;
export const SAMPLE_URL = "/run-it-sample.json";
export const SCHEMA = "run-it.state/1";

export interface Factor { value: number; unit: string; source: string; override: boolean }
export interface TimelinePoint { t: number; app: number | null; user: number | null; system: number | null }
export interface RunItProcess { pid: number; name: string; cpu: number; watts: number | null; joules: number | null; rss: number; in_app: boolean }
export interface OwnerScan { status: string; seconds: number; findings: number }
export interface RunItFinding { check_id: string; pattern: string | null; confidence: "high" | "medium" | "low"; file: string | null; line: number | null; summary: string }

export interface RunItState {
  schema: string;
  status: "running" | "stopping";
  session: { id: string; started_at: string; elapsed_s: number; interval_s: number; mode: "run" | "watch"; label: string; folder: string; platform: string };
  app: { pid: number; running: boolean; exit_code: number | null; processes: number } | null;
  measured: boolean;
  power_w: { app: number | null; user: number | null; system: number | null; system_age_s: number | null };
  energy_j: { app: number | null; user: number; system: number };
  readable: { processes: number; total: number };
  impact: { scope: "app" | "user"; scope_label: string; joules: number; co2_g: number; water_ml: number } | null;
  system_impact: { joules: number; co2_g: number; water_ml: number } | null;
  factors: { grid: Factor; water: Factor };
  timeline: TimelinePoint[];
  app_breakdown?: { name: string; joules: number; processes: number }[];
  processes: RunItProcess[];
  scan: {
    state: "off" | "starting" | "running" | "done";
    owners: Record<string, "running" | OwnerScan>;
    findings_total: number;
    by_confidence: Partial<Record<"high" | "medium" | "low", number>>;
    checks_total: number | null;
    top: RunItFinding[];
  };
}

/** live: polling run-it; stopped: run-it went away, last reading kept; searching: never connected yet (sample shown). */
export type LinkStatus = "searching" | "live" | "stopped";

export function isRunItState(data: unknown): data is RunItState {
  const d = data as RunItState | null;
  return Boolean(d && d.schema === SCHEMA && d.session && d.factors && Array.isArray(d.timeline) && Array.isArray(d.processes) && d.scan);
}

// ---------- formatting (same rules as run_it/impact.py) ----------

const J_PER_KWH = 3.6e6;

export function fmtEnergy(joules: number): string {
  if (joules < 3600) return `${joules.toLocaleString("en-US", { maximumFractionDigits: joules < 10 ? 1 : 0 })} J`;
  if (joules < J_PER_KWH) return `${(joules / 3600).toFixed(2)} Wh`;
  return `${(joules / J_PER_KWH).toFixed(3)} kWh`;
}

export function fmtMass(grams: number): string {
  if (grams < 1) return `${(grams * 1000).toFixed(grams < 0.01 ? 2 : 1)} mg`;
  return grams < 1000 ? `${grams.toFixed(2)} g` : `${(grams / 1000).toFixed(2)} kg`;
}

export function fmtVolume(ml: number): string {
  if (ml < 1) return `~${(ml * 1000).toFixed(0)} µL`;
  return ml < 1000 ? `~${ml.toFixed(1)} mL` : `~${(ml / 1000).toFixed(2)} L`;
}

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m ${String(sec).padStart(2, "0")}s` : `${m}m ${String(sec).padStart(2, "0")}s`;
}

export function fmtWatts(watts: number | null | undefined): string {
  return watts === null || watts === undefined ? "—" : `${watts.toFixed(watts < 10 ? 2 : 1)} W`;
}

// ---------- live link ----------

const SEARCH_MS = 3000;
const REQUEST_MS = 1500;
const FAILURES_BEFORE_STOPPED = 3;

async function fetchState(port: number, signal: AbortSignal): Promise<RunItState> {
  const timeout = AbortSignal.timeout(REQUEST_MS);
  const response = await fetch(`http://127.0.0.1:${port}/state`, { cache: "no-store", signal: AbortSignal.any([signal, timeout]) });
  if (!response.ok) throw new Error(`run-it answered HTTP ${response.status}`);
  const data: unknown = await response.json();
  if (!isRunItState(data)) throw new Error("not a run-it state");
  return data;
}

export interface RunItLink {
  status: LinkStatus;
  /** The latest live reading (live or stopped). */
  live: RunItState | null;
  /** When the latest live reading arrived (ms since epoch). */
  seenAt: number | null;
  sample: RunItState | null;
  sampleError: string | null;
}

/**
 * Polls run-it at its own sampling interval while connected, and looks for it every few seconds otherwise.
 * Polling pauses while the tab is hidden. A new run-it session replaces the previous reading.
 */
export function useRunItLink(port: number): RunItLink {
  const [link, setLink] = useState<RunItLink>({ status: "searching", live: null, seenAt: null, sample: null, sampleError: null });
  const status = useRef<LinkStatus>("searching");

  useEffect(() => {
    const controller = new AbortController();
    fetch(SAMPLE_URL, { signal: controller.signal })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<unknown>; })
      .then((data) => {
        if (!isRunItState(data)) throw new Error("the bundled file is not a run-it session");
        setLink((l) => ({ ...l, sample: data }));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLink((l) => ({ ...l, sampleError: error instanceof Error ? error.message : String(error) }));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const schedule = (ms: number) => { timer = setTimeout(tick, ms); };
    async function tick() {
      if (controller.signal.aborted) return;
      if (document.hidden) return schedule(1000);
      try {
        const state = await fetchState(port, controller.signal);
        failures = 0;
        status.current = "live";
        setLink((l) => ({ ...l, status: "live", live: state, seenAt: Date.now() }));
        schedule(Math.max(500, state.session.interval_s * 1000));
      } catch {
        if (controller.signal.aborted) return;
        failures += 1;
        if (status.current === "live" && failures >= FAILURES_BEFORE_STOPPED) {
          status.current = "stopped";
          setLink((l) => ({ ...l, status: "stopped" }));
        }
        schedule(status.current === "live" ? 1000 : SEARCH_MS);
      }
    }
    void tick();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [port]);

  return link;
}
