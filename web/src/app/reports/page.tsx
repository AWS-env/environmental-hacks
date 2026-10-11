"use client";

import React, { useState } from "react";
import Link from "next/link";
import ThemeToggle from "@/components/theme/ThemeToggle";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";


interface HistoricalScan {
  commit: string;
  shortSha: string;
  branch: string;
  timestamp: string;
  trigger: "push-webhook" | "periodic-nightly" | "ci-collector";
  materialChange: boolean;
  energyKwh: number;
  carbonKg: number;
  wasteKwh: number;
  activeFindings: number;
  summary: string;
}

interface WasteDriver {
  id: string;
  checkId: string;
  title: string;
  category: "database" | "iteration" | "network" | "resource";
  file: string;
  line: string;
  confidence: "high" | "medium" | "low";
  estimatedWasteKwh: string;
  explanation: string;
  reference: string;
  prompt: string;
}

const HISTORICAL_SCANS: HistoricalScan[] = [
  {
    commit: "4438dee42191467e54f625dbd8ddd48cdadc1016",
    shortSha: "4438dee",
    branch: "main",
    timestamp: "2026-10-10 03:45 UTC",
    trigger: "push-webhook",
    materialChange: false,
    energyKwh: 164.8,
    carbonKg: 4.61,
    wasteKwh: 27.8,
    activeFindings: 6,
    summary: "Refactored CSS styles & assets — no material change to compute topology or query patterns.",
  },
  {
    commit: "9a1c22f08e4210941829e01824ab198751fa3321",
    shortSha: "9a1c22f",
    branch: "main",
    timestamp: "2026-10-09 21:15 UTC",
    trigger: "push-webhook",
    materialChange: true,
    energyKwh: 168.2,
    carbonKg: 4.71,
    wasteKwh: 31.4,
    activeFindings: 7,
    summary: "Removed unbounded pagination loop in telemetry ingestion pipeline (saved ~12 kWh/mo).",
  },
  {
    commit: "7e5021a88b1401820491823901bfa28394819283",
    shortSha: "7e5021a",
    branch: "main",
    timestamp: "2026-10-09 00:00 UTC",
    trigger: "periodic-nightly",
    materialChange: false,
    energyKwh: 180.2,
    carbonKg: 5.04,
    wasteKwh: 43.4,
    activeFindings: 8,
    summary: "Nightly EventBridge cron: AWS telemetry sync matched historical average.",
  },
  {
    commit: "0d40b6eee4e04fe8c6be54655d6d2d3d4bce42d7",
    shortSha: "0d40b6e",
    branch: "main",
    timestamp: "2026-10-08 14:20 UTC",
    trigger: "push-webhook",
    materialChange: true,
    energyKwh: 182.4,
    carbonKg: 5.11,
    wasteKwh: 45.6,
    activeFindings: 9,
    summary: "Initial baseline scan across repository source, cloud metrics, and configuration.",
  },
];

const WASTE_DRIVERS: WasteDriver[] = [
  {
    id: "wd-1",
    checkId: "DB-01",
    title: "N+1 Query Pattern in Sensor Batch Processor",
    category: "database",
    file: "src/services/sensorIngestion.ts",
    line: "142-158",
    confidence: "high",
    estimatedWasteKwh: "14.2 – 18.5 kWh / mo",
    explanation:
      "Repeated per-item database queries inside an async loop cause excessive database round-trips, elevated RDS vCPU burn, and connection pool pressure without providing additional freshness.",
    reference: "Green Software Foundation • Pattern: Batch DB Operations",
    prompt:
      "Refactor src/services/sensorIngestion.ts lines 142-158: Replace the per-sensor individual SELECT/INSERT queries inside the loop with a single bulk chunk query (using parameterized batching) to eliminate N+1 roundtrips and reduce database compute waste.",
  },
  {
    id: "wd-2",
    checkId: "CODE-C3.4",
    title: "Nested Quadratic Iteration on Unindexed Arrays",
    category: "iteration",
    file: "src/utils/telemetryAggregator.ts",
    line: "78-95",
    confidence: "high",
    estimatedWasteKwh: "7.8 – 12.1 kWh / mo",
    explanation:
      "Nested O(N²) array lookups over incoming sample streams increase CPU cycles by 38x compared to an indexed Map lookup under sustained request volume.",
    reference: "Taxonomy Check CODE-C3.4: Nested Iteration Complexity",
    prompt:
      "Refactor src/utils/telemetryAggregator.ts lines 78-95: Convert the nested Array.find inside the map loop into a single pre-indexed Map/Dictionary lookup (O(N) instead of O(N²)) to prevent CPU saturation on incoming batches.",
  },
  {
    id: "wd-3",
    checkId: "NET-02",
    title: "Uncompressed High-Frequency API Response Payloads",
    category: "network",
    file: "src/api/metricsExport.ts",
    line: "44-52",
    confidence: "medium",
    estimatedWasteKwh: "4.4 – 8.6 kWh / mo",
    explanation:
      "Raw JSON telemetry exports exceeding 450 KB are transmitted without gzip/brotli compression headers, causing avoidable network egress and client decompression latency.",
    reference: "W3C Best Practices • Network Transmission & Payload Waste",
    prompt:
      "Update src/api/metricsExport.ts lines 44-52 to enable Brotli / Gzip response stream compression for telemetry payloads above 1 KB, reducing egress transfer volume and network interface power consumption.",
  },
  {
    id: "wd-4",
    checkId: "RES-03",
    title: "Overprovisioned Idle Memory Buffer in Lambda Handlers",
    category: "resource",
    file: "infra/serverless/functions.yaml",
    line: "28-34",
    confidence: "medium",
    estimatedWasteKwh: "2.1 – 4.9 kWh / mo",
    explanation:
      "Lambda configured with 2048 MB memory allocations whereas telemetry confirms p99 memory utilization never exceeds 192 MB, leading to unnecessary memory reservation charges.",
    reference: "AWS Compute Optimizer • Serverless Sizing Guidelines",
    prompt:
      "In infra/serverless/functions.yaml lines 28-34, right-size the Lambda memory allocation from 2048MB to 512MB based on confirmed p99 telemetry utilization of 184MB, optimizing allocated gigabyte-seconds compute waste.",
  },
];

export default function ReportsPage() {
  const [selectedScan, setSelectedScan] = useState<HistoricalScan>(HISTORICAL_SCANS[0]);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [carbonBudget, setCarbonBudget] = useState<number>(7.5); // kg CO2e / mo
  const [activeTab, setActiveTab] = useState<"summary" | "provenance" | "history" | "waste">("summary");
  const [copiedPromptSuccess, setCopiedPromptSuccess] = useState<string | null>(null);

  const copyPrompt = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setCopiedPromptSuccess("Prompt copied to clipboard! Paste it directly into your coding agent.");
    setTimeout(() => {
      setCopiedId(null);
      setCopiedPromptSuccess(null);
    }, 2500);
  };

  const copyRawReport = () => {
    const reportData = {
      project: "environmental-hacks",
      schema: "ecoaudit-sustainability-report/v1",
      generatedAt: new Date().toISOString(),
      methodology: "GSF-SCI-2024",
      scanDetails: selectedScan,
      estimates: {
        energyKwhMonth: { min: 148.2, expected: selectedScan.energyKwh, max: 186.5 },
        carbonKgMonth: { min: 4.15, expected: selectedScan.carbonKg, max: 5.22 },
        sciIntensityPerRequestGrams: { min: 0.041, expected: 0.046, max: 0.052 },
        avoidableWasteKwhMonth: { min: 21.0, expected: selectedScan.wasteKwh, max: 38.5 },
      },
      provenance: {
        cloudProvider: "AWS (eu-north-1 / Stockholm)",
        gridEmissionFactor: "28 g CO2e / kWh (Nordic Grid Mix)",
        accessType: "Read-Only STS AssumeRole (Telemetry & Logs only)",
        executionModel: "Zero-Execution Guarantee (Code never run)",
      },
      topFindings: WASTE_DRIVERS,
    };
    navigator.clipboard.writeText(JSON.stringify(reportData, null, 2));
    setCopiedPromptSuccess("Full report.json payload copied to clipboard!");
    setTimeout(() => setCopiedPromptSuccess(null), 2500);
  };

  // Budget consumption percentage
  const budgetPercent = Math.min(100, Math.round((selectedScan.carbonKg / carbonBudget) * 100));

  return (
    <div className="min-h-screen bg-page text-ink selection:bg-accent selection:text-on-accent flex flex-col justify-between">
      {/* Light theme sky backdrop */}
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />

      {/* Night theme ambient glows */}
      <div className="pointer-events-none fixed -top-40 -left-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div className="pointer-events-none fixed top-1/3 -right-40 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />

      {/* Main Header / Navigation */}
      <header className="relative z-20 border-b border-line bg-surface-2/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <Link href="/" className="group flex items-center gap-3 transition-opacity hover:opacity-90">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 shadow-lg shadow-emerald-500/20">
                <svg className="h-5 w-5 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <div>
                <span className="font-bold text-base tracking-tight text-ink">EcoAudit</span>
                <span className="text-xs text-ink-3 ml-2 font-mono">/reports</span>
              </div>
            </Link>

            <span className="hidden sm:inline-block rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-semibold text-accent-strong border border-accent-line">
              GSF Software Carbon Intensity (SCI)
            </span>
          </div>

          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            >
              Workstation Login
            </Link>
            <ThemeToggle />
            <Magnetic>
              <button
                type="button"
                onClick={copyRawReport}
                className="group flex items-center gap-2 rounded-lg bg-accent px-3.5 py-1.5 text-xs font-semibold text-on-accent shadow-sm transition-all hover:bg-accent-hover active:scale-95"
              >
                <RollText>Export report.json</RollText>
              </button>
            </Magnetic>
          </div>
        </div>
      </header>

      {/* Feedback Toast */}
      {copiedPromptSuccess && (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-xl border border-accent-line bg-surface-3/95 px-4 py-3 text-xs font-medium text-ink shadow-2xl backdrop-blur-md transition-all animate-in fade-in slide-in-from-bottom-2"
        >
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-600 dark:text-emerald-400">
            ✓
          </span>
          {copiedPromptSuccess}
        </div>
      )}

      {/* Main Container */}
      <main className="relative z-10 mx-auto w-full max-w-7xl px-4 py-8 md:px-6">
        {/* Page Top Banner */}
        <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-strong mb-3">
              <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
              Two-Clock Estimate Engine • Version v1.4-nordic-arm64
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight text-ink md:text-4xl">
              Software Sustainability & Compute Waste Report
            </h1>
            <p className="mt-1.5 max-w-3xl text-xs md:text-sm text-ink-3">
              Range-based environmental estimation combining static repository checks, read-only AWS telemetry, and user workload baselines under a strict zero-execution trust boundary.
            </p>
          </div>

          {/* Quick Commit / Reassessment Info */}
          <div className="rounded-xl border border-line bg-surface-2 p-3.5 text-xs shadow-sm">
            <div className="flex items-center justify-between gap-4 mb-1">
              <span className="text-ink-3 font-mono text-[11px]">Active Evaluation:</span>
              <span className="font-mono font-semibold text-ink">commit {selectedScan.shortSha}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="font-medium text-ink">
                Materiality Gate:{" "}
                <strong className={selectedScan.materialChange ? "text-amber-500" : "text-emerald-600 dark:text-emerald-400"}>
                  {selectedScan.materialChange ? "Material Impact Shift" : "No Material Change"}
                </strong>
              </span>
            </div>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="mb-6 flex flex-wrap gap-2 border-b border-line pb-3">
          <button
            type="button"
            onClick={() => setActiveTab("summary")}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all ${
              activeTab === "summary"
                ? "bg-accent text-on-accent shadow-sm"
                : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
            }`}
          >
            Impact Summary & Ranges
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("waste")}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all ${
              activeTab === "waste"
                ? "bg-accent text-on-accent shadow-sm"
                : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
            }`}
          >
            Avoidable Compute Waste ({WASTE_DRIVERS.length} Drivers)
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("provenance")}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all ${
              activeTab === "provenance"
                ? "bg-accent text-on-accent shadow-sm"
                : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
            }`}
          >
            Trust & Provenance Matrix
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("history")}
            className={`rounded-lg px-4 py-2 text-xs font-semibold transition-all ${
              activeTab === "history"
                ? "bg-accent text-on-accent shadow-sm"
                : "bg-surface-2 text-ink-3 hover:bg-surface-3 hover:text-ink"
            }`}
          >
            Commit Comparison & Scans
          </button>
        </div>

        {/* TAB 1: SUMMARY & IMPACT RANGES */}
        {activeTab === "summary" && (
          <div className="space-y-6">
            {/* Top Cards Grid */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Card 1: Energy Consumption */}
              <div className="rounded-2xl border border-line bg-surface-2/90 p-5 shadow-sm backdrop-blur-md transition-all hover:border-accent-line">
                <div className="flex items-center justify-between text-xs text-ink-3 mb-2">
                  <span className="font-semibold uppercase tracking-wider">Operational Energy</span>
                  <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">
                    High Confidence
                  </span>
                </div>
                <div className="text-2xl font-extrabold text-ink font-mono">
                  {selectedScan.energyKwh} <span className="text-sm font-normal text-ink-3">kWh / mo</span>
                </div>
                <div className="mt-2 text-xs text-ink-3">
                  Estimated Range:{" "}
                  <strong className="text-ink font-mono font-medium">148.2 – 186.5 kWh</strong>
                </div>
                <div className="mt-3 flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                  </svg>
                  <span>6.4% below 30-day baseline</span>
                </div>
              </div>

              {/* Card 2: Operational Carbon Footprint */}
              <div className="rounded-2xl border border-line bg-surface-2/90 p-5 shadow-sm backdrop-blur-md transition-all hover:border-accent-line">
                <div className="flex items-center justify-between text-xs text-ink-3 mb-2">
                  <span className="font-semibold uppercase tracking-wider">Carbon Footprint</span>
                  <span className="rounded bg-accent-soft px-2 py-0.5 text-[10px] font-bold text-accent-strong">
                    eu-north-1 Grid
                  </span>
                </div>
                <div className="text-2xl font-extrabold text-ink font-mono">
                  {selectedScan.carbonKg} <span className="text-sm font-normal text-ink-3">kg CO₂e</span>
                </div>
                <div className="mt-2 text-xs text-ink-3">
                  Estimated Range:{" "}
                  <strong className="text-ink font-mono font-medium">4.15 – 5.22 kg CO₂e</strong>
                </div>
                <div className="mt-3 text-[11px] text-ink-4">
                  Factor: 28 g CO₂e/kWh (Nordic low-carbon grid)
                </div>
              </div>

              {/* Card 3: Avoidable Compute Waste */}
              <div className="rounded-2xl border border-line bg-surface-2/90 p-5 shadow-sm backdrop-blur-md transition-all hover:border-accent-line">
                <div className="flex items-center justify-between text-xs text-ink-3 mb-2">
                  <span className="font-semibold uppercase tracking-wider">Avoidable Waste</span>
                  <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold text-amber-600 dark:text-amber-400">
                    Actionable
                  </span>
                </div>
                <div className="text-2xl font-extrabold text-amber-600 dark:text-amber-400 font-mono">
                  {selectedScan.wasteKwh} <span className="text-sm font-normal text-ink-3">kWh / mo</span>
                </div>
                <div className="mt-2 text-xs text-ink-3">
                  Share of Compute:{" "}
                  <strong className="text-amber-600 dark:text-amber-400 font-mono">
                    {Math.round((selectedScan.wasteKwh / selectedScan.energyKwh) * 100)}% potential saving
                  </strong>
                </div>
                <div className="mt-3 text-[11px] text-ink-3">
                  Identified across {selectedScan.activeFindings} code & query findings
                </div>
              </div>

              {/* Card 4: Functional Unit Intensity (SCI) */}
              <div className="rounded-2xl border border-line bg-surface-2/90 p-5 shadow-sm backdrop-blur-md transition-all hover:border-accent-line">
                <div className="flex items-center justify-between text-xs text-ink-3 mb-2">
                  <span className="font-semibold uppercase tracking-wider">SCI per Request</span>
                  <span className="rounded bg-surface-3 px-2 py-0.5 text-[10px] font-bold text-ink-2">
                    R = 100k req/mo
                  </span>
                </div>
                <div className="text-2xl font-extrabold text-ink font-mono">
                  0.046 <span className="text-sm font-normal text-ink-3">g CO₂e / req</span>
                </div>
                <div className="mt-2 text-xs text-ink-3">
                  Range: <strong className="text-ink font-mono font-medium">0.041 – 0.052 g</strong>
                </div>
                <div className="mt-3 text-[11px] text-ink-4">
                  Standard GSF Formula: (E × I + M) / R
                </div>
              </div>
            </div>

            {/* Middle Section: Carbon Budget Meter & Regional Details */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              {/* Carbon Budget Tracker */}
              <div className="lg:col-span-2 rounded-2xl border border-line bg-surface-2/90 p-6 shadow-sm backdrop-blur-md">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-base font-bold text-ink">Monthly Carbon Budget Tracking</h2>
                    <p className="text-xs text-ink-3">
                      EventBridge Scheduler triggers an SNS alert (Email/Slack) when emissions project over budget.
                    </p>
                  </div>
                  <div className="text-right font-mono text-xs">
                    <span className="text-ink-3">Target: </span>
                    <strong className="text-ink">{carbonBudget} kg CO₂e</strong>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="space-y-2">
                  <div className="flex justify-between text-xs font-semibold">
                    <span>
                      {selectedScan.carbonKg} kg CO₂e consumed ({budgetPercent}%)
                    </span>
                    <span className="text-ink-3">{(carbonBudget - selectedScan.carbonKg).toFixed(2)} kg headroom</span>
                  </div>
                  <div className="h-3 w-full overflow-hidden rounded-full bg-surface-3">
                    <div
                      className={`h-full transition-all duration-500 ${
                        budgetPercent > 90 ? "bg-red-500" : budgetPercent > 75 ? "bg-amber-500" : "bg-emerald-500"
                      }`}
                      style={{ width: `${budgetPercent}%` }}
                    />
                  </div>
                </div>

                {/* Interactive Slider */}
                <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-line/60 pt-4 text-xs">
                  <label htmlFor="budget-slider" className="text-ink-3">
                    Adjust Carbon Budget Threshold:
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      id="budget-slider"
                      type="range"
                      min="4.0"
                      max="15.0"
                      step="0.5"
                      value={carbonBudget}
                      onChange={(e) => setCarbonBudget(parseFloat(e.target.value))}
                      className="cursor-pointer accent-accent"
                    />
                    <span className="w-16 font-mono font-bold text-ink">{carbonBudget.toFixed(1)} kg</span>
                  </div>
                </div>
              </div>

              {/* Regional & Eco Metadata */}
              <div className="rounded-2xl border border-line bg-surface-2/90 p-6 shadow-sm backdrop-blur-md">
                <h2 className="text-base font-bold text-ink mb-3">Auditor Perimeter & Region</h2>
                <div className="space-y-3 text-xs">
                  <div className="flex justify-between border-b border-line/60 pb-2">
                    <span className="text-ink-3">Deployment Region:</span>
                    <span className="font-mono font-semibold text-ink">eu-north-1 (Stockholm)</span>
                  </div>
                  <div className="flex justify-between border-b border-line/60 pb-2">
                    <span className="text-ink-3">Water Footprint (WUE):</span>
                    <span className="font-mono font-semibold text-ink">0.38 – 0.54 L / mo</span>
                  </div>
                  <div className="flex justify-between border-b border-line/60 pb-2">
                    <span className="text-ink-3">Embodied Carbon (M):</span>
                    <span className="font-mono font-semibold text-ink">1.82 kg CO₂e / yr (Amortized)</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-3">Read-Only Guardrail:</span>
                    <span className="font-semibold text-emerald-600 dark:text-emerald-400">Zero Execution Verified</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: AVOIDABLE COMPUTE WASTE DRIVERS */}
        {activeTab === "waste" && (
          <div className="space-y-4">
            <div className="rounded-xl border border-accent-line bg-accent-soft p-4 text-xs text-accent-ink mb-2">
              <strong className="font-bold">Remediation Workflow:</strong> Each finding below represents demonstrable software compute waste. Click <strong>[Copy Agent Prompt]</strong> to export an isolated context prompt directly to your coding assistant (Copilot, Cursor, Claude) without re-indexing your whole repository.
            </div>

            <div className="grid grid-cols-1 gap-4">
              {WASTE_DRIVERS.map((driver) => (
                <div
                  key={driver.id}
                  className="rounded-2xl border border-line bg-surface-2/90 p-5 shadow-sm backdrop-blur-md transition-all hover:border-ink-4"
                >
                  <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-surface-3 px-2 py-0.5 font-mono text-[11px] font-bold text-ink">
                          {driver.checkId}
                        </span>
                        <h3 className="font-bold text-sm text-ink">{driver.title}</h3>
                        <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-600 dark:text-amber-400 border border-amber-500/20">
                          {driver.confidence.toUpperCase()} CONFIDENCE
                        </span>
                      </div>
                      <p className="text-xs text-ink-3 font-mono">
                        {driver.file}:{driver.line}
                      </p>
                    </div>

                    <div className="flex items-center gap-3">
                      <div className="text-right sm:text-right">
                        <span className="text-[10px] text-ink-4 uppercase tracking-wider block">Estimated Waste</span>
                        <span className="font-mono text-xs font-bold text-amber-600 dark:text-amber-400">
                          {driver.estimatedWasteKwh}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => copyPrompt(driver.id, driver.prompt)}
                        className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent transition-all hover:bg-accent-hover active:scale-95 flex items-center gap-1.5 shadow-sm"
                      >
                        {copiedId === driver.id ? "✓ Copied!" : "Copy Agent Prompt"}
                      </button>
                    </div>
                  </div>

                  <p className="mt-3 text-xs text-ink-2 leading-relaxed">{driver.explanation}</p>

                  <div className="mt-3 flex items-center justify-between border-t border-line/50 pt-2 text-[11px] text-ink-4">
                    <span>Source: {driver.reference}</span>
                    <span className="font-mono text-[10px]">Read-only static verification</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TAB 3: PROVENANCE & TRANSPARENCY MATRIX */}
        {activeTab === "provenance" && (
          <div className="rounded-2xl border border-line bg-surface-2/90 p-6 shadow-sm backdrop-blur-md space-y-6">
            <div>
              <h2 className="text-base font-bold text-ink">Strict Data Provenance & Reporting Boundaries</h2>
              <p className="text-xs text-ink-3 mt-1">
                Per Section 5 of the Software Sustainability Auditor charter: measured telemetry, user-provided values, standard conversion factors, and code-level hypotheses are rigorously partitioned.
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-line text-ink-3 uppercase tracking-wider font-semibold">
                    <th className="py-3 px-3">Data Dimension</th>
                    <th className="py-3 px-3">Evidence Source</th>
                    <th className="py-3 px-3">Methodology / Protocol</th>
                    <th className="py-3 px-3">Boundary & Limits</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/60 text-ink-2">
                  <tr>
                    <td className="py-3 px-3 font-semibold text-ink">Host Telemetry</td>
                    <td className="py-3 px-3 font-mono text-[11px]">AWS CloudWatch (STS AssumeRole)</td>
                    <td className="py-3 px-3">Read-only retrieval of vCPU utilization and memory p99</td>
                    <td className="py-3 px-3 text-ink-3">No test workloads or synthetic benchmarking executed</td>
                  </tr>
                  <tr>
                    <td className="py-3 px-3 font-semibold text-ink">Code Inefficiencies</td>
                    <td className="py-3 px-3 font-mono text-[11px]">Static Scanner (AST & Semgrep)</td>
                    <td className="py-3 px-3">Taxonomy checks over repository source & configs</td>
                    <td className="py-3 px-3 text-ink-3">Unverified runtime metrics marked unavailable</td>
                  </tr>
                  <tr>
                    <td className="py-3 px-3 font-semibold text-ink">Regional Grid Carbon</td>
                    <td className="py-3 px-3 font-mono text-[11px]">IEA / GSF SCI Emissions Factor</td>
                    <td className="py-3 px-3">28 g CO₂e/kWh based on Nordic power grid mix (Sweden)</td>
                    <td className="py-3 px-3 text-ink-3">Location-based operational emissions scope</td>
                  </tr>
                  <tr>
                    <td className="py-3 px-3 font-semibold text-ink">Functional Unit (R)</td>
                    <td className="py-3 px-3 font-mono text-[11px]">User Input / Workload Configuration</td>
                    <td className="py-3 px-3">100,000 HTTP API transactions per month</td>
                    <td className="py-3 px-3 text-ink-3">Normalized against real traffic scale</td>
                  </tr>
                  <tr>
                    <td className="py-3 px-3 font-semibold text-ink">Embodied Carbon (M)</td>
                    <td className="py-3 px-3 font-mono text-[11px]">ISO 14040/14044 LCA Modelling</td>
                    <td className="py-3 px-3">Hardware lifecycle amortization (4-year server lifetime)</td>
                    <td className="py-3 px-3 text-ink-3">Excludes network transit hops outside cloud edge</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="rounded-xl border border-line bg-surface p-4 text-xs text-ink-3">
              <strong className="text-ink">Reporting Rule:</strong> Never claim an exact energy or carbon value for an individual line of code without direct physical measurement. Estimates are provided as <strong>defensible ranges</strong> with transparent confidence levels.
            </div>
          </div>
        )}

        {/* TAB 4: COMMIT COMPARISON & SCANS */}
        {activeTab === "history" && (
          <div className="space-y-4">
            <div>
              <h2 className="text-base font-bold text-ink">Historical Scan Trail & Commit Comparison</h2>
              <p className="text-xs text-ink-3">
                Scans trigger automatically via GitHub push webhooks or nightly EventBridge schedulers. Estimates recompute only when a change exceeds the materiality threshold.
              </p>
            </div>

            <div className="space-y-3">
              {HISTORICAL_SCANS.map((scan) => (
                <div
                  key={scan.commit}
                  onClick={() => setSelectedScan(scan)}
                  className={`cursor-pointer rounded-2xl border p-4.5 transition-all shadow-sm ${
                    selectedScan.commit === scan.commit
                      ? "border-accent bg-surface-3 ring-1 ring-accent"
                      : "border-line bg-surface-2 hover:bg-surface-3"
                  }`}
                >
                  <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs font-bold text-ink bg-surface px-2 py-0.5 rounded border border-line">
                        {scan.shortSha}
                      </span>
                      <span className="text-xs font-semibold text-ink">{scan.summary}</span>
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                          scan.materialChange
                            ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                            : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        }`}
                      >
                        {scan.materialChange ? "Material Shift" : "No Material Change"}
                      </span>
                      <span className="font-mono font-semibold text-ink">{scan.energyKwh} kWh</span>
                      <span className="font-mono text-ink-3">{scan.timestamp}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="relative z-10 border-t border-line bg-surface-2/60 py-6 text-center text-xs text-ink-4">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between px-6">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink">EcoAudit</span>
            <span>•</span>
            <span>Read-Only Software Sustainability Auditor</span>
          </div>
          <div className="flex items-center gap-4 text-[11px]">
            <span>AWS Environmental Hacks</span>
            <span>•</span>
            <span>Green Software Foundation SCI</span>
            <span>•</span>
            <span>Zero-Execution Trust Architecture</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
