"use client";

import type { ReactNode } from "react";
import type { CheckStatus, Confidence, Evidence } from "@/lib/scan-api";

export const STATUS_META: Record<CheckStatus, { label: string; icon: string; note: string; color: string }> = {
  completed: { label: "Completed", icon: "✓", note: "Whole scope evaluated", color: "#8fd1a8" },
  partial: { label: "Partial", icon: "◐", note: "Some files not evaluated", color: "#e5c07b" },
  unavailable: { label: "Unavailable", icon: "!", note: "Evidence missing; nothing evaluated", color: "#8a979d" },
  error: { label: "Error", icon: "✕", note: "Crashed or invalid output", color: "#f0908a" },
  not_applicable: { label: "Not applicable", icon: "–", note: "No files this check examines", color: "#56636a" },
};

/** Confidence is ordinal: one hue (the app's gold) in three steps, always shown with its label. */
export const CONFIDENCE_COLOR: Record<Confidence, string> = { high: "#f7c062", medium: "#b89356", low: "#7f7158" };

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
/** Taxonomy layer ids (docs/taxonomy/checks.json `layer_label`) as display names. */
export const layerName = (layer: string) => ({ ci: "CI", llm: "LLM" } as Record<string, string>)[layer] ?? cap(layer);
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
    <span aria-hidden="true" className="rp-status-icon" style={{ color: meta.color, background: `${meta.color}22` }}>
      {meta.icon}
    </span>
  );
}

export function StatusBadge({ status }: { status: CheckStatus }) {
  return <span className="rp-status"><StatusIcon status={status} />{STATUS_META[status].label}</span>;
}

export function ConfidenceDot({ confidence }: { confidence: Confidence }) {
  return <span aria-hidden="true" className="rp-dot" style={{ background: CONFIDENCE_COLOR[confidence] }} />;
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return <span className="rp-pill"><ConfidenceDot confidence={confidence} />{cap(confidence)} confidence</span>;
}

export function Panel({ title, hint, icon, right, children, className = "", bodyClassName = "rp-panel-body", labelledBy }: {
  title?: string; hint?: ReactNode; icon?: ReactNode; right?: ReactNode; children: ReactNode;
  className?: string; bodyClassName?: string; labelledBy?: string;
}) {
  return (
    <section className={`rp-panel ${className}`} aria-labelledby={title ? labelledBy : undefined}>
      {title && (
        <div className="rp-panel-head">
          {icon && <span className="rp-icon">{icon}</span>}
          <div className="min-w-0">
            <h2 id={labelledBy}>{title}</h2>
            {hint && <p>{hint}</p>}
          </div>
          {right && <div className="rp-head-right">{right}</div>}
        </div>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

/** A segmented stacked bar with a legend underneath: every segment's value is also printed. */
export function StackedBar({ parts, label }: { parts: { key: string; label: string; value: number; color: string }[]; label: string }) {
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  return (
    <div className="flex flex-col gap-2.5">
      <div className="rp-stack" role="img" aria-label={`${label}: ${parts.map((p) => `${p.value} ${p.label}`).join(", ")}`}>
        {total > 0 && parts.filter((p) => p.value > 0).map((p) => (
          <i key={p.key} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="rp-legend">
        {parts.map((p) => (
          <span key={p.key}><span className="rp-dot" style={{ background: p.color }} aria-hidden="true" />{p.label} <b>{fmt(p.value)}</b></span>
        ))}
      </div>
    </div>
  );
}

/** Quoted repository source. Rendered as text only: it is untrusted content from the scanned repository. */
export function EvidenceBlock({ item }: { item: Evidence }) {
  const label = item.line_start !== undefined ? `${item.locator}:${item.line_start}` : item.field ? `${item.locator} [${item.field}]` : item.locator;
  const text = typeof item.value === "string" ? item.value : JSON.stringify(item.value, null, 2);
  return (
    <figure className="rp-code">
      <figcaption className="rp-code-head"><span className="truncate">{label}</span><span className="uppercase tracking-wider">{item.kind}</span></figcaption>
      <pre>
        {text.split("\n").map((line, i) => (
          <div key={i}>
            {item.line_start !== undefined && <span className="ln">{item.line_start + i}</span>}
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
