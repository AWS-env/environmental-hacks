"use client";

import React, { useState } from "react";

interface Finding {
  id: string;
  checkId: string;
  title: string;
  category: "Code Inefficiencies" | "Database & Queries" | "Infrastructure" | "Observability & Tests";
  owner: "Owner A" | "Owner B" | "Owner C" | "Owner D";
  file: string;
  line: number;
  confidence: "high" | "medium" | "low";
  description: string;
  codeSnippet: string;
  recommendation: string;
}

const SAMPLE_FINDINGS: Finding[] = [
  {
    id: "f-1",
    checkId: "CODE-C3.7",
    title: "Array Mutation inside Hot Loop",
    category: "Code Inefficiencies",
    owner: "Owner A",
    file: "src/services/data-processor.ts",
    line: 84,
    confidence: "high",
    description: "Repeated array mutation inside an iteration construct leads to excessive heap allocations and GC pressure.",
    codeSnippet: "for (const item of dataset) {\n  results.push(transform(item)); // Re-allocates dynamic backing array\n}",
    recommendation: "Pre-allocate array buffer or use map()/generator stream to avoid repeated buffer reallocations.",
  },
  {
    id: "f-2",
    checkId: "DB-05",
    title: "N+1 Query Pattern in Entity Relational Map",
    category: "Database & Queries",
    owner: "Owner B",
    file: "backend/models/user_history.py",
    line: 142,
    confidence: "high",
    description: "Iterative single-record queries trigger unnecessary round trips to RDS Aurora instance, multiplying compute energy.",
    codeSnippet: "for user in users:\n  profile = db.query(Profile).filter(Profile.user_id == user.id).first()",
    recommendation: "Use eager loading (joinedload/in_ batch query) to retrieve all records in a single round trip.",
  },
  {
    id: "f-3",
    checkId: "INF-09",
    title: "Oversized Container Image (Unstripped Toolchain)",
    category: "Infrastructure",
    owner: "Owner D",
    file: "deploy/Dockerfile",
    line: 12,
    confidence: "medium",
    description: "Base container includes complete GCC compiler toolchain and package cache, resulting in 1.4GB image transfer on each pod scale-up.",
    codeSnippet: "FROM golang:1.22\nCOPY . .\nRUN go build -o app .",
    recommendation: "Adopt multi-stage Docker build with scratch or distroless minimal base image.",
  },
  {
    id: "f-4",
    checkId: "OBS-02",
    title: "Eager String Interpolation in Debug Logs",
    category: "Observability & Tests",
    owner: "Owner D",
    file: "handlers/ingestion.js",
    line: 56,
    confidence: "high",
    description: "JSON.stringify() and template literals are calculated regardless of whether debug level logging is active.",
    codeSnippet: "logger.debug(`Ingested payload: ${JSON.stringify(heavyPayload)}`);",
    recommendation: "Pass log context lazily as structured parameters: logger.debug('Ingested payload', () => heavyPayload);",
  },
];

export default function Home() {
  const [selectedCategory, setSelectedCategory] = useState<string>("All");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const categories = ["All", "Code Inefficiencies", "Database & Queries", "Infrastructure", "Observability & Tests"];

  const filteredFindings = SAMPLE_FINDINGS.filter((f) => {
    const matchesCategory = selectedCategory === "All" || f.category === selectedCategory;
    const matchesQuery =
      f.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      f.checkId.toLowerCase().includes(searchQuery.toLowerCase()) ||
      f.file.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesCategory && matchesQuery;
  });

  const handleCopyAgentPrompt = (finding: Finding) => {
    const prompt = `Please refactor ${finding.file} around line ${finding.line} to fix ${finding.checkId} (${finding.title}).\nRecommendation: ${finding.recommendation}`;
    navigator.clipboard.writeText(prompt);
    setCopiedId(finding.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="min-h-screen bg-[#090d16] text-gray-100 selection:bg-emerald-500 selection:text-black">
      {/* Glow Effects */}
      <div className="pointer-events-none fixed -top-40 -left-40 h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl" />
      <div className="pointer-events-none fixed top-1/3 -right-40 h-96 w-96 rounded-full bg-teal-500/10 blur-3xl" />

      {/* Navigation */}
      <header className="sticky top-0 z-50 border-b border-white/10 bg-[#090d16]/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 shadow-lg shadow-emerald-500/20">
              <svg className="h-6 w-6 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-lg tracking-tight text-white">EcoAudit</span>
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs font-semibold text-emerald-400 border border-emerald-500/30">
                  AWS Environmental Hacks
                </span>
              </div>
              <p className="text-xs text-gray-400">Software Sustainability & Compute Waste Auditor</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <a
              href="https://github.com/AWS-env/environmental-hacks"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-gray-300 transition-colors hover:bg-white/10 hover:text-white"
            >
              <span>GitHub Repo</span>
            </a>
            <button
              type="button"
              id="upload-report-btn"
              className="rounded-lg bg-emerald-500 px-4 py-1.5 text-xs font-semibold text-black shadow-md shadow-emerald-500/20 transition-all hover:bg-emerald-400 active:scale-95"
            >
              Upload report.json
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-10 space-y-10">
        {/* Hero Section */}
        <section className="relative rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.04] to-transparent p-8 md:p-12 overflow-hidden shadow-2xl">
          <div className="relative z-10 max-w-3xl space-y-4">
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Contract v1 Compliant • Live Scanning Active
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight sm:text-5xl text-white">
              Eliminate Software Compute Waste & Carbon Footprint
            </h1>
            <p className="text-base sm:text-lg text-gray-400 leading-relaxed">
              Automated read-only auditor discovering architectural inefficiencies, hot loop mutations, and unoptimized cloud resources before they turn into server energy waste.
            </p>
          </div>
        </section>

        {/* Aggregate KPI Metrics */}
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm">
            <div className="flex items-center justify-between text-gray-400 text-xs uppercase font-medium">
              <span>Total Findings</span>
              <span className="text-emerald-400">● Live</span>
            </div>
            <div className="mt-3 text-3xl font-bold text-white">42</div>
            <p className="mt-1 text-xs text-gray-400">Across 18 scanned services</p>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm">
            <div className="flex items-center justify-between text-gray-400 text-xs uppercase font-medium">
              <span>High Confidence</span>
              <span className="text-amber-400">78%</span>
            </div>
            <div className="mt-3 text-3xl font-bold text-emerald-400">29</div>
            <p className="mt-1 text-xs text-gray-400">Verified with cited line evidence</p>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm">
            <div className="flex items-center justify-between text-gray-400 text-xs uppercase font-medium">
              <span>Active Detectors</span>
              <span className="text-teal-400">Contract v1</span>
            </div>
            <div className="mt-3 text-3xl font-bold text-teal-400">4 Owners</div>
            <p className="mt-1 text-xs text-gray-400">Code, DB, Runtime, Infra & OBS</p>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm">
            <div className="flex items-center justify-between text-gray-400 text-xs uppercase font-medium">
              <span>Est. Energy Savings</span>
              <span className="text-emerald-400">Annual</span>
            </div>
            <div className="mt-3 text-3xl font-bold text-emerald-300">~18.4%</div>
            <p className="mt-1 text-xs text-gray-400">Projected CPU cycle reduction</p>
          </div>
        </section>

        {/* Explorer & Filters */}
        <section className="space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              {categories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setSelectedCategory(cat)}
                  className={`rounded-lg px-3.5 py-1.5 text-xs font-medium transition-all ${
                    selectedCategory === cat
                      ? "bg-emerald-500 text-black font-semibold shadow-md shadow-emerald-500/20"
                      : "border border-white/10 bg-white/5 text-gray-300 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>

            <div className="relative">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter by check, file, or keyword..."
                className="w-full sm:w-72 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-gray-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>
          </div>

          {/* Findings Cards */}
          <div className="space-y-4">
            {filteredFindings.map((finding) => (
              <article
                key={finding.id}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-6 transition-all hover:border-emerald-500/40 hover:bg-white/[0.04]"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-emerald-500/20 px-2 py-0.5 font-mono text-xs font-semibold text-emerald-300 border border-emerald-500/30">
                        {finding.checkId}
                      </span>
                      <span className="rounded bg-white/10 px-2 py-0.5 text-xs font-medium text-gray-300">
                        {finding.owner}
                      </span>
                      <span className="rounded bg-teal-500/20 px-2 py-0.5 text-xs text-teal-300">
                        {finding.category}
                      </span>
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          finding.confidence === "high"
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-amber-500/20 text-amber-300"
                        }`}
                      >
                        {finding.confidence} confidence
                      </span>
                    </div>
                    <h2 className="text-lg font-bold text-white pt-1">{finding.title}</h2>
                    <p className="text-xs font-mono text-gray-400">
                      {finding.file} : line {finding.line}
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={() => handleCopyAgentPrompt(finding)}
                    className="self-start rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-emerald-500 hover:text-black transition-all"
                  >
                    {copiedId === finding.id ? "✓ Copied to clipboard" : "Copy Agent Prompt"}
                  </button>
                </div>

                <p className="mt-3 text-sm text-gray-300">{finding.description}</p>

                {/* Evidence snippet */}
                <div className="mt-4 rounded-lg border border-white/5 bg-black/60 p-3 font-mono text-xs text-emerald-300 overflow-x-auto">
                  <pre>{finding.codeSnippet}</pre>
                </div>

                {/* Recommendation */}
                <div className="mt-3 flex items-start gap-2 rounded-lg bg-emerald-500/10 p-3 text-xs text-emerald-300 border border-emerald-500/20">
                  <span className="font-semibold text-emerald-400">Remediation:</span>
                  <span>{finding.recommendation}</span>
                </div>
              </article>
            ))}

            {filteredFindings.length === 0 && (
              <div className="rounded-xl border border-dashed border-white/10 p-12 text-center text-sm text-gray-400">
                No findings match the current filter criteria.
              </div>
            )}
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="mt-20 border-t border-white/10 bg-[#090d16]/60 py-8 text-center text-xs text-gray-500">
        <p>Built for WeMakeDevs × AWS Environmental Hacks • Powered by Next.js & Tailwind CSS</p>
      </footer>
    </div>
  );
}
