"use client";

import React, { useState } from "react";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";

interface FindingItem {
  id: string;
  category: string;
  title: string;
  location: string;
  confidence: "Static Pattern" | "Runtime-Confirmed" | "Telemetry Inferred";
  confidenceBadge: string;
  impactRange: string;
  impactUnit: string;
  whyItMatters: string;
  snippet: string;
  references: string[];
  agentPrompt: string;
}

const SAMPLE_FINDINGS: FindingItem[] = [
  {
    id: "n-plus-one",
    category: "Database & I/O Waste",
    title: "N+1 Query Pattern in Unbatched Loop",
    location: "src/api/routes/orders.ts:74",
    confidence: "Static Pattern",
    confidenceBadge: "AST Pattern (Semgrep)",
    impactRange: "0.14 – 0.42 g CO₂e",
    impactUnit: "per 100 requests (4.2x baseline I/O overhead)",
    whyItMatters:
      "A database query is dispatched inside an iterative forEach loop rather than batched with an IN clause or JOIN. Multiplies network round-trips and keeps database connection threads active unnecessarily.",
    snippet: `// Inefficient: queries DB once per order item
const items = await getOrderItems(orderId);
for (const item of items) {
  const stock = await db.query('SELECT stock FROM inventory WHERE id = $1', [item.id]);
}`,
    references: [
      "Green Software Foundation: Software Carbon Intensity (SCI) Standard",
      "AWS Well-Architected Framework: Sustainability Pillar (SUS 4.1)",
    ],
    agentPrompt: `You are optimizing code for software sustainability and compute efficiency.
Finding: N+1 Database Query Pattern in src/api/routes/orders.ts:74
Evidence: An unbatched database query is invoked inside a loop over order items.
Goal: Refactor this query to fetch all item stocks in a single batched query using 'WHERE id = ANY($1)' or bulk IN clause.
Preserve exact return semantics and add typing.`,
  },
  {
    id: "memory-leak",
    category: "Memory Retention",
    title: "Unbounded Buffer Accumulation in Event Worker",
    location: "src/workers/stream_consumer.ts:128",
    confidence: "Runtime-Confirmed",
    confidenceBadge: "Runtime-Confirmed (memray)",
    impactRange: "1.8 – 3.2 kWh",
    impactUnit: "per worker / month in sustained heap pressure",
    whyItMatters:
      "Incoming telemetry buffers are appended to an array without periodic compaction or bounded ring-buffer limits. Causes V8 garbage collector to execute frequent high-latency scavenges and keeps memory footprint bloated.",
    snippet: `// Unbounded cache: retained objects prevent GC sweeps
const eventHistory = [];
function onIncomingEvent(evt: TelemetryEvent) {
  eventHistory.push(evt); // Never pruned or flushed!
  processTelemetry(evt);
}`,
    references: [
      "Node.js Memory Diagnostics & V8 GC Compaction Guidelines",
      "ISO 14040/14044 Life Cycle Assessment Principles",
    ],
    agentPrompt: `You are optimizing memory footprint and GC compute overhead.
Finding: Unbounded Buffer Accumulation in src/workers/stream_consumer.ts:128
Evidence: Retained event array grows monotonically without bounds, forcing high GC cycle frequency.
Goal: Replace 'eventHistory' with a fixed-size ring buffer (capacity 500) or an LRU cache with time-to-live eviction.`,
  },
  {
    id: "cold-concurrency",
    category: "Cloud Resource Waste",
    title: "Overprovisioned Lambda Concurrency on Low-Throughput Route",
    location: "infra/serverless.yml:45",
    confidence: "Telemetry Inferred",
    confidenceBadge: "CloudWatch Telemetry (90 days)",
    impactRange: "18.4 – 26.0 kg CO₂e",
    impactUnit: "per month idle warm container reservation",
    whyItMatters:
      "Provisioned Concurrency is statically set to 40 warm instances for a webhook endpoint that averages 0.08 requests/second with 99.4% idle uptime. Idle containers waste allocated EC2 microVM capacity behind Firecracker.",
    snippet: `functions:
  webhookHandler:
    handler: src/webhook.handler
    provisionedConcurrency: 40 # 99% idle over 90 days!`,
    references: [
      "AWS Compute Optimizer Rightsizing Engine",
      "Green Software Principles: Carbon Efficiency through Dynamic Provisioning",
    ],
    agentPrompt: `You are optimizing cloud infrastructure efficiency to eliminate idle compute waste.
Finding: Overprovisioned Lambda Concurrency in infra/serverless.yml:45
Evidence: ProvisionedConcurrency=40 maintains 99.4% idle microVM reservations based on 90-day CloudWatch metrics.
Goal: Remove static provisionedConcurrency or configure Application Auto Scaling with min=1, max=5 based on Utilization metric.`,
  },
];

export default function InteractiveFindingPreview() {
  const [selectedId, setSelectedId] = useState(SAMPLE_FINDINGS[0].id);
  const [copied, setCopied] = useState(false);

  const finding = SAMPLE_FINDINGS.find((f) => f.id === selectedId) ?? SAMPLE_FINDINGS[0];

  const handleCopyPrompt = () => {
    navigator.clipboard.writeText(finding.agentPrompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2400);
  };

  return (
    <section id="findings-preview" className="relative py-24 px-6 border-t border-line/60">
      <div className="mx-auto max-w-7xl">
        {/* Section Header */}
        <div className="max-w-3xl mb-16" data-reveal>
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Evidence-Backed Findings
          </div>
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-ink mb-4" data-split>
            Actionable Findings. Zero Hallucinations.
          </h2>
          <p className="text-ink-3 text-base md:text-lg leading-relaxed">
            Every audit surfaces exact file locations, defensible environmental impact ranges, and a pre-engineered prompt ready for your AI coding assistant (Cursor, Claude Code, Gemini CLI).
          </p>
        </div>

        {/* Tab Buttons */}
        <div className="flex flex-wrap gap-2.5 mb-8" data-reveal>
          {SAMPLE_FINDINGS.map((f) => {
            const isSelected = f.id === selectedId;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => {
                  setSelectedId(f.id);
                  setCopied(false);
                }}
                className={`rounded-xl px-4 py-2 text-xs font-semibold transition-all duration-200 border ${
                  isSelected
                    ? "bg-accent text-on-accent border-accent shadow-md shadow-accent/20"
                    : "bg-surface text-ink-2 border-line hover:bg-surface-2 hover:text-ink"
                }`}
              >
                {f.category}
              </button>
            );
          })}
        </div>

        {/* Finding Card Mock */}
        <div
          data-reveal
          className="rounded-3xl border border-line bg-surface p-6 sm:p-10 backdrop-blur-xl shadow-xl transition-all duration-300"
        >
          {/* Top metadata row */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-line">
            <div className="space-y-1">
              <div className="flex items-center gap-2.5 flex-wrap">
                <span className="rounded-md bg-accent-soft px-2 py-0.5 text-xs font-mono font-semibold text-accent-strong border border-accent-line">
                  {finding.location}
                </span>
                <span className="rounded-md bg-surface-3 px-2 py-0.5 text-xs font-medium text-ink-2">
                  {finding.confidenceBadge}
                </span>
              </div>
              <h3 className="text-xl sm:text-2xl font-bold text-ink mt-2">{finding.title}</h3>
            </div>

            {/* Impact Metric Badge */}
            <div className="flex flex-col sm:items-end justify-center rounded-2xl border border-accent-line bg-accent-soft/40 p-4">
              <span className="text-[11px] font-medium text-ink-3 uppercase tracking-wider">Estimated Waste</span>
              <span className="text-lg sm:text-xl font-extrabold text-accent-strong font-mono">{finding.impactRange}</span>
              <span className="text-[11px] text-ink-3 text-right">{finding.impactUnit}</span>
            </div>
          </div>

          {/* Details & Code Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 pt-8">
            {/* Explanation & References */}
            <div className="lg:col-span-6 space-y-6">
              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-ink-3 mb-2">Why It Matters</h4>
                <p className="text-sm text-ink-2 leading-relaxed">{finding.whyItMatters}</p>
              </div>

              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-ink-3 mb-2">Credible Citations</h4>
                <ul className="space-y-1.5 text-xs text-ink-3">
                  {finding.references.map((ref) => (
                    <li key={ref} className="flex items-center gap-2">
                      <span className="text-accent-strong">↗</span>
                      <span>{ref}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Action Banner */}
              <div className="rounded-2xl border border-line bg-surface-2 p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <div className="font-semibold text-ink text-sm">Pass Direct Context to Your AI Agent</div>
                  <div className="text-xs text-ink-3">Zero token waste investigating the entire repo.</div>
                </div>

                <Magnetic>
                  <button
                    type="button"
                    onClick={handleCopyPrompt}
                    className={`rounded-xl px-4 py-2.5 text-xs font-bold transition-all flex items-center gap-2 shadow-md ${
                      copied
                        ? "bg-emerald-500 text-white shadow-emerald-500/20"
                        : "bg-accent text-on-accent hover:bg-accent-hover shadow-accent/20 active:scale-95"
                    }`}
                  >
                    {copied ? (
                      <>
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                        </svg>
                        <RollText>Prompt Copied!</RollText>
                      </>
                    ) : (
                      <>
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"
                          />
                        </svg>
                        <RollText>Copy Prompt for Coding Agent</RollText>
                      </>
                    )}
                  </button>
                </Magnetic>
              </div>
            </div>

            {/* Code Snippet & Copied Prompt Preview */}
            <div className="lg:col-span-6 space-y-4">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-ink-3">Source Evidence</span>
                  <span className="text-[11px] font-mono text-ink-3">{finding.location}</span>
                </div>
                <div className="overflow-hidden rounded-2xl border border-line bg-page/95 p-4 font-mono text-xs text-ink-2 shadow-inner">
                  <pre className="overflow-x-auto text-[11px] leading-relaxed text-ink-2">{finding.snippet}</pre>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-ink-3">Agent-Ready Prompt Preview</span>
                  <span className="text-[11px] text-accent-strong font-medium">Curated Context</span>
                </div>
                <div className="overflow-hidden rounded-2xl border border-line bg-surface-2/60 p-4 font-mono text-[11px] text-ink-3 max-h-36 overflow-y-auto leading-relaxed">
                  <pre className="whitespace-pre-wrap">{finding.agentPrompt}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
