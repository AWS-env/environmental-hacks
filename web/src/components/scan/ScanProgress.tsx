"use client";

import { useEffect, useState } from "react";
import ScanPipeline from "@/components/ScanPipeline";
import { SCAN_TIMEOUT_MS } from "@/lib/scan-api";
import type { ScanPhase, ScanSessionState } from "@/lib/scan-session";

const PHASE_TEXT: Partial<Record<ScanPhase, { title: string; detail: string }>> = {
  submitting: { title: "Submitting", detail: "Sending the repository to scan-api" },
  queued: { title: "Queued", detail: "Waiting for a scan worker" },
  running: { title: "Scanning", detail: "Downloading the repository and running every check" },
  loading_report: { title: "Loading report", detail: "Fetching the finished report" },
};

export function useEscape(onEscape: () => void) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onEscape();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onEscape]);
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * An in-flight scan: #443's illustrative pipeline over the terrain. Its status bar shows the real scan phase
 * and elapsed time; it is the only status display and the only Back button while a scan runs.
 */
export default function ScanProgress({ state, step, onBack }: { state: ScanSessionState; step: number; onBack: () => void }) {
  const now = useNow();
  useEscape(onBack);
  const text = PHASE_TEXT[state.phase] ?? { title: "Working", detail: "" };
  return (
    <ScanPipeline
      step={step}
      repository={state.repoUrl}
      status={{
        title: text.title,
        detail: state.scanId ? `${text.detail} · scan ${state.scanId.slice(0, 8)}` : text.detail,
        meta: `${formatElapsed(now - state.startedAt)} · limit ${SCAN_TIMEOUT_MS / 60_000} min`,
      }}
      onBack={onBack}
    />
  );
}
