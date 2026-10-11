"use client";

import type { ReactNode } from "react";
import type { CheckStatus, Confidence, Evidence } from "@/lib/scan-api";

export const card = "rounded-2xl border border-line bg-surface-2/90 shadow-sm backdrop-blur-md";

export const STATUS_META: Record<CheckStatus, { label: string; icon: string; note: string; tone: string }> = {
  completed: { label: "Completed", icon: "✓", note: "whole scope evaluated", tone: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  partial: { label: "Partial", icon: "◐", note: "some files not evaluated", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  unavailable: { label: "Unavailable", icon: "!", note: "evidence missing; nothing evaluated", tone: "bg-slate-500/15 text-slate-700 dark:text-slate-300" },
  error: { label: "Error", icon: "✕", note: "crashed or invalid output", tone: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
  not_applicable: { label: "Not applicable", icon: "–", note: "no files this check examines", tone: "bg-slate-500/10 text-ink-3" },
};

/** Confidence is ordinal, so one hue in three steps (stronger = more confident); always shown with its label. */
export const CONFIDENCE_SWATCH: Record<Confidence, string> = {
  high: "bg-amber-700 dark:bg-amber-300",
  medium: "bg-amber-500 dark:bg-amber-500",
  low: "bg-amber-300 dark:bg-amber-800",
};

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const fmt = (n: number | null | undefined) => Number(n ?? 0).toLocaleString("en-US");
export const plural = (n: number, word: string) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "unknown time";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function StatusIcon({ status }: { status: CheckStatus }) {
  const meta = STATUS_META[status];
  return (
    <span aria-hidden="true" className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${meta.tone}`}>
      {meta.icon}
    </span>
  );
}

export function StatusBadge({ status }: { status: CheckStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-ink">
      <StatusIcon status={status} />
      {STATUS_META[status].label}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-surface px-2 py-0.5 text-[11px] font-semibold text-ink-2">
      <span aria-hidden="true" className={`h-2 w-2 rounded-sm ${CONFIDENCE_SWATCH[confidence]}`} />
      {cap(confidence)} confidence
    </span>
  );
}

export function Pill({ children, mono, title }: { children: ReactNode; mono?: boolean; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded-full border border-line bg-surface px-2 py-0.5 text-[11px] text-ink-2 ${mono ? "font-mono" : "font-medium"}`}>
      {children}
    </span>
  );
}

export function Tile({ label, value, sub, lead, hero }: { label: string; value: number; sub?: string; lead?: ReactNode; hero?: boolean }) {
  return (
    <div className={`${card} p-4`}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
        {lead}
        <span>{label}</span>
      </div>
      <div className={`mt-1.5 font-mono font-extrabold tabular-nums ${hero ? "text-3xl" : "text-2xl"} ${value ? "text-ink" : "text-ink-4"}`}>
        {fmt(value)}
      </div>
      {sub && <div className="mt-1 text-[11px] leading-snug text-ink-3">{sub}</div>}
    </div>
  );
}

export function SectionTitle({ title, hint, right }: { title: string; hint?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 className="text-base font-bold text-ink">{title}</h2>
        {hint && <p className="mt-0.5 text-xs text-ink-3">{hint}</p>}
      </div>
      {right}
    </div>
  );
}

/** Quoted repository source. Rendered as text only: it is untrusted content from the scanned repository. */
export function EvidenceBlock({ item }: { item: Evidence }) {
  const label = item.line_start !== undefined ? `${item.locator}:${item.line_start}` : item.field ? `${item.locator} [${item.field}]` : item.locator;
  const text = typeof item.value === "string" ? item.value : JSON.stringify(item.value, null, 2);
  const lines = text.split("\n");
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-line bg-surface">
      <figcaption className="flex items-center justify-between gap-2 border-b border-line px-3 py-1.5 font-mono text-[11px] text-ink-3">
        <span className="truncate">{label}</span>
        <span className="shrink-0 uppercase tracking-wider">{item.kind}</span>
      </figcaption>
      <pre className="max-h-80 overflow-auto px-3 py-2 font-mono text-xs leading-relaxed text-ink">
        {lines.map((line, i) => (
          <div key={i} className="flex gap-3">
            {item.line_start !== undefined && (
              <span className="min-w-[3ch] select-none text-right tabular-nums text-ink-4">{item.line_start + i}</span>
            )}
            <code className="whitespace-pre">{line || " "}</code>
          </div>
        ))}
      </pre>
    </figure>
  );
}

/** Clipboard API first; falls back to a hidden textarea (older browsers, non-secure contexts). */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

export const selectClass =
  "rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink focus:border-accent-strong focus:outline-none focus:ring-1 focus:ring-accent-strong";

export const chipClass = (active: boolean) =>
  `rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-strong ${
    active ? "bg-accent text-on-accent shadow-sm" : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
  }`;
