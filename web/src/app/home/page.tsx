"use client";

import React, { useEffect, useState } from "react";
import { Bell, ChevronDown } from "lucide-react";
import LiquidGlassInput from "@/components/LiquidGlassInput";
import ParticleTerrain from "@/components/ParticleTerrain";
import ScanPanel from "@/components/scan/ScanPanel";
import ScanProgress from "@/components/scan/ScanProgress";
import { ACTIVE_PHASES, startScan, useScanSession, type ScanSession } from "@/lib/scan-session";

export default function HomePage() {
  const [repoUrl, setRepoUrl] = useState("");
  const [scan, setScan] = useState<ScanSession | null>(null);
  const scanState = useScanSession(scan);
  const isAnalyzing = scanState !== null && ACTIVE_PHASES.includes(scanState.phase);
  const [scanStep, setScanStep] = useState(-1);
  useEffect(() => () => scan?.cancel(), [scan]);

  const sampleRepos = [
    { name: "vercel/next.js", url: "https://github.com/vercel/next.js" },
    { name: "facebook/react", url: "https://github.com/facebook/react" },
    { name: "langchain-ai/langchain", url: "https://github.com/langchain-ai/langchain" },
    { name: "microsoft/vscode", url: "https://github.com/microsoft/vscode" },
  ];

  const startAnalysis = (urlToAnalyze?: string) => {
    const target = (urlToAnalyze || repoUrl).trim();
    if (!target || scan) return;
    setScanStep(-1);
    setScan(startScan(target));
  };

  const closeScan = () => {
    scan?.cancel();
    setScan(null);
  };

  return (
    <>
    <ParticleTerrain analyzing={isAnalyzing} onStageChange={setScanStep} />
    <div className="relative z-10 h-screen max-h-screen overflow-y-auto flex flex-col py-4 px-4 md:px-8 text-white">
      {/* Top Header Bar */}
      <header className="flex items-center justify-between pb-2 shrink-0 border-b border-white/[0.04]">
        {/* Left Brand */}
        <div className="flex items-center gap-2">
          <span className="text-xl font-bold tracking-tight text-white font-sans drop-shadow-sm">
            Kimi
          </span>
        </div>

        {/* Right Status & Profile Controls */}
        <div className="flex items-center gap-3">
          {/* Workspace Switcher */}
          <button className="flex items-center gap-2 relative liquid-glass rounded-full border border-white/[0.08] px-3.5 py-1 text-xs font-medium text-zinc-200 hover:bg-white/[0.06] transition-colors shadow-sm">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]" />
            <span className="text-[11px]">Personal</span>
            <ChevronDown className="w-3 h-3 text-zinc-400" />
          </button>

          {/* Notifications */}
          <button className="flex items-center justify-center w-7 h-7 relative liquid-glass rounded-full border border-white/[0.08] text-zinc-300 hover:text-white hover:bg-white/[0.06] transition-colors">
            <Bell className="w-3.5 h-3.5" />
          </button>

          {/* Avatar */}
          <div className="w-7 h-7 rounded-full bg-[#35435a] border border-white/10 text-white flex items-center justify-center font-semibold text-xs shadow-md">
            S
          </div>
        </div>
      </header>

      {/* Main Content Area strictly budgeted vertically */}
      <main className={`home-hero flex-1 ${scan ? "is-departing" : ""}`} aria-hidden={scan !== null} inert={scan !== null} hidden={scanState !== null && !isAnalyzing}>
        {/* Hero Title & Subtitle */}
        <div className="home-copy text-center">
          <h1 className="hero-title font-medium tracking-tight text-white">
            Turn any repository into{" "}
            <span className="text-[#f7c062]">
              insights.
            </span>
          </h1>
          <p className="mt-3 text-sm text-zinc-200 mx-auto leading-relaxed">
            Understand, analyze, and work with your codebase using AI.
          </p>
        </div>

        {/* Central Apple Liquid Glass Repository Input Box */}
        <div className="shrink-0 w-full">
          <LiquidGlassInput
            repoUrl={repoUrl}
            setRepoUrl={setRepoUrl}
            onAnalyze={startAnalysis}
            isAnalyzing={isAnalyzing}
            sampleRepos={sampleRepos}
          />
        </div>

        <div aria-hidden="true" />
      </main>
      {scanState && (isAnalyzing
        ? <ScanProgress state={scanState} step={scanStep} onBack={closeScan} />
        : <ScanPanel state={scanState} onClose={closeScan} />)}
    </div>
    </>
  );
}
