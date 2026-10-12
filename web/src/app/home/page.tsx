"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
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
  const [activeSteps, setActiveSteps] = useState<number[]>([]);
  useEffect(() => () => scan?.cancel(), [scan]);

  const sampleRepos = [
    { name: "fastapi/full-stack-fastapi-template", url: "https://github.com/fastapi/full-stack-fastapi-template" },
    { name: "simonw/llm", url: "https://github.com/simonw/llm" },
    { name: "pallets/flask", url: "https://github.com/pallets/flask" },
    { name: "AWS-env/environmental-hacks", url: "https://github.com/AWS-env/environmental-hacks" },
  ];

  const startAnalysis = (urlToAnalyze?: string) => {
    const target = (urlToAnalyze || repoUrl).trim();
    if (!target || scan) return;
    setScanStep(-1);
    setActiveSteps([]);
    setScan(startScan(target));
  };

  const closeScan = () => {
    scan?.cancel();
    setScan(null);
  };

  return (
    <>
    <ParticleTerrain analyzing={isAnalyzing} onStageChange={(step, active) => { setScanStep(step); setActiveSteps(active); }} />
    <div className="relative z-10 h-screen max-h-screen overflow-y-auto flex flex-col py-4 px-4 md:px-8 text-white">
      {/* Top Header Bar */}
      <header className="flex items-center pb-2 shrink-0 border-b border-white/[0.04]">
        <Link href="/" className="text-xl font-bold tracking-tight text-white font-sans drop-shadow-sm">
          EcoAudit
        </Link>
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
        ? <ScanProgress state={scanState} step={scanStep} activeSteps={activeSteps} onBack={closeScan} />
        : <ScanPanel state={scanState} onClose={closeScan} />)}
    </div>
    </>
  );
}
