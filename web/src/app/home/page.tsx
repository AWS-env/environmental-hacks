"use client";

import React, { useState } from "react";
import {
  Bell,
  ChevronDown,
  Folder,
  FileCode2,
  FileText,
  Search,
  FileEdit,
  Sparkles,
  BarChart2,
  Compass,
  Boxes,
  GitBranch,
  Lightbulb,
  Wand2,
} from "lucide-react";
import LiquidGlassInput from "@/components/LiquidGlassInput";

export default function HomePage() {
  const [repoUrl, setRepoUrl] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisStep, setAnalysisStep] = useState(0);

  const sampleRepos = [
    { name: "vercel/next.js", url: "https://github.com/vercel/next.js" },
    { name: "facebook/react", url: "https://github.com/facebook/react" },
    { name: "langchain-ai/langchain", url: "https://github.com/langchain-ai/langchain" },
    { name: "microsoft/vscode", url: "https://github.com/microsoft/vscode" },
  ];

  const startAnalysis = (urlToAnalyze?: string) => {
    const target = urlToAnalyze || repoUrl;
    if (!target) return;
    setIsAnalyzing(true);
    setAnalysisStep(1);

    setTimeout(() => setAnalysisStep(2), 1100);
    setTimeout(() => setAnalysisStep(3), 2200);
    setTimeout(() => {
      setIsAnalyzing(false);
      setAnalysisStep(4);
    }, 3300);
  };

  return (
    <div className="h-screen max-h-screen overflow-hidden flex flex-col justify-between py-3 pr-6 pl-2 text-zinc-200 select-none">
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
          <button className="flex items-center gap-2 rounded-full bg-[#162220]/80 border border-white/[0.08] px-3.5 py-1 text-xs font-medium text-zinc-200 hover:bg-white/[0.06] transition-colors shadow-sm">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]" />
            <span className="text-[11px]">Personal</span>
            <ChevronDown className="w-3 h-3 text-zinc-400" />
          </button>

          {/* Notifications */}
          <button className="flex items-center justify-center w-7 h-7 rounded-full bg-[#162220]/80 border border-white/[0.08] text-zinc-300 hover:text-white hover:bg-white/[0.06] transition-colors">
            <Bell className="w-3.5 h-3.5" />
          </button>

          {/* Avatar */}
          <div className="w-7 h-7 rounded-full bg-[#35435a] border border-white/10 text-white flex items-center justify-center font-semibold text-xs shadow-md">
            S
          </div>
        </div>
      </header>

      {/* Main Content Area strictly budgeted vertically */}
      <main className="flex-1 min-h-0 flex flex-col justify-between pt-1 pb-1">
        {/* Hero Title & Subtitle */}
        <div className="text-center shrink-0">
          <h1 className="text-3xl md:text-4xl font-normal tracking-tight text-white leading-tight">
            Turn any repository <br />
            into{" "}
            <span className="bg-gradient-to-r from-[#ffd480] via-[#f7c062] to-[#df9724] bg-clip-text text-transparent font-medium drop-shadow-[0_0_20px_rgba(247,192,98,0.3)]">
              insights.
            </span>
          </h1>
          <p className="mt-1 text-xs text-zinc-400 max-w-lg mx-auto font-normal">
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

        {/* Central Visual Pipeline: Floating Glass Cards & Golden Energy Wave */}
        <div className="relative shrink-0 w-full max-w-4xl mx-auto h-[148px] flex items-center justify-between px-2">
          {/* Background Golden Energy Wave SVG */}
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none z-0"
            viewBox="0 0 800 148"
            fill="none"
          >
            {/* Ambient glow stroke */}
            <path
              d="M 160 74 C 260 20, 340 120, 430 74 C 520 28, 590 120, 680 74"
              stroke="#f7c062"
              strokeWidth="4"
              strokeOpacity="0.25"
              strokeLinecap="round"
              className="blur-sm"
            />
            {/* Core luminous fiber strand */}
            <path
              d="M 160 74 C 260 20, 340 120, 430 74 C 520 28, 590 120, 680 74"
              stroke="#ffe299"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeDasharray="6 4"
              className={isAnalyzing ? "animate-pulse" : ""}
            />
          </svg>

          {/* Left Floating Card: Repository Tree */}
          <div className="relative z-10 w-[170px] rounded-2xl bg-[#192523]/80 border border-white/[0.1] p-3 backdrop-blur-md shadow-2xl transition-transform hover:scale-102">
            <div className="flex items-center gap-2 pb-2 border-b border-white/[0.06]">
              <svg className="w-3.5 h-3.5 text-zinc-300" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
              </svg>
              <span className="text-xs font-semibold text-white">Repository</span>
            </div>
            <div className="space-y-1 pt-2 font-mono text-[10px] text-zinc-300">
              <div className="flex items-center gap-1.5 text-amber-300/90">
                <Folder className="w-3 h-3 text-amber-400" />
                <span>src</span>
              </div>
              <div className="flex items-center gap-1.5 pl-2.5 text-zinc-400">
                <Folder className="w-2.5 h-2.5 text-amber-400/80" />
                <span>components</span>
              </div>
              <div className="flex items-center gap-1.5 pl-2.5 text-zinc-400">
                <Folder className="w-2.5 h-2.5 text-amber-400/80" />
                <span>lib</span>
              </div>
              <div className="flex items-center gap-1.5 pl-2.5 text-zinc-400">
                <Folder className="w-2.5 h-2.5 text-amber-400/80" />
                <span>api</span>
              </div>
              <div className="flex items-center gap-1.5 text-teal-300/80">
                <FileText className="w-3 h-3 text-teal-400" />
                <span>README.md</span>
              </div>
            </div>
          </div>

          {/* Node 1: Analyze */}
          <div
            className={`relative z-10 w-[95px] rounded-xl bg-[#192523]/80 border p-2 backdrop-blur-md shadow-xl text-center transition-all ${
              analysisStep === 1
                ? "border-amber-400 shadow-[0_0_15px_rgba(247,192,98,0.5)] scale-105"
                : "border-white/[0.08]"
            }`}
          >
            <div className="flex items-center justify-center gap-1 text-[10px] font-medium text-zinc-300 pb-1 border-b border-white/[0.06]">
              <Wand2 className="w-2.5 h-2.5 text-amber-300" />
              <span>Analyze</span>
            </div>
            <div className="pt-2 flex flex-col items-center gap-1">
              <div className="w-2 h-2 rounded-full bg-teal-400 shadow-[0_0_4px_rgba(45,212,191,0.8)]" />
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
                <div className="w-1.5 h-1.5 rounded-full bg-purple-400" />
              </div>
              <div className="w-8 h-0.5 bg-white/10 rounded" />
            </div>
          </div>

          {/* Node 2: Understand */}
          <div
            className={`relative z-10 w-[95px] rounded-xl bg-[#192523]/80 border p-2 backdrop-blur-md shadow-xl text-center transition-all ${
              analysisStep === 2
                ? "border-amber-400 shadow-[0_0_15px_rgba(247,192,98,0.5)] scale-105"
                : "border-white/[0.08]"
            }`}
          >
            <div className="flex items-center justify-center gap-1 text-[10px] font-medium text-zinc-300 pb-1 border-b border-white/[0.06]">
              <FileCode2 className="w-2.5 h-2.5 text-cyan-300" />
              <span>Understand</span>
            </div>
            <div className="pt-2 space-y-1">
              <div className="w-12 h-1 bg-cyan-400/80 rounded mx-auto" />
              <div className="w-8 h-1 bg-teal-400/60 rounded mx-auto" />
              <div className="w-10 h-1 bg-emerald-400/70 rounded mx-auto" />
            </div>
          </div>

          {/* Node 3: Generate */}
          <div
            className={`relative z-10 w-[95px] rounded-xl bg-[#192523]/80 border p-2 backdrop-blur-md shadow-xl text-center transition-all ${
              analysisStep === 3
                ? "border-amber-400 shadow-[0_0_15px_rgba(247,192,98,0.5)] scale-105"
                : "border-white/[0.08]"
            }`}
          >
            <div className="flex items-center justify-center gap-1 text-[10px] font-medium text-zinc-300 pb-1 border-b border-white/[0.06]">
              <Sparkles className="w-2.5 h-2.5 text-purple-300" />
              <span>Generate</span>
            </div>
            <div className="pt-2 space-y-1">
              <div className="w-10 h-1 bg-purple-400/80 rounded mx-auto" />
              <div className="w-12 h-1 bg-pink-400/60 rounded mx-auto" />
              <div className="w-7 h-1 bg-indigo-400/70 rounded mx-auto" />
            </div>
          </div>

          {/* Right Floating Card: Insights */}
          <div className="relative z-10 w-[170px] rounded-2xl bg-[#192523]/80 border border-white/[0.1] p-3 backdrop-blur-md shadow-2xl transition-transform hover:scale-102">
            <div className="flex items-center gap-2 pb-2 border-b border-white/[0.06]">
              <BarChart2 className="w-3.5 h-3.5 text-amber-300" />
              <span className="text-xs font-semibold text-white">Insights</span>
            </div>
            <div className="space-y-1.5 pt-2 text-[10px] text-zinc-300">
              <div className="flex items-center gap-1.5">
                <Compass className="w-3 h-3 text-cyan-400 shrink-0" />
                <span className="truncate">Architecture</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Boxes className="w-3 h-3 text-purple-400 shrink-0" />
                <span className="truncate">Key components</span>
              </div>
              <div className="flex items-center gap-1.5">
                <GitBranch className="w-3 h-3 text-teal-400 shrink-0" />
                <span className="truncate">Dependencies</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Lightbulb className="w-3 h-3 text-amber-400 shrink-0" />
                <span className="truncate">Improvement ideas</span>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom 4 Feature Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 shrink-0 max-w-5xl mx-auto w-full">
          {/* Card 1: Codebase Understanding */}
          <div className="rounded-2xl bg-[#192523]/80 border border-white/[0.08] p-3.5 backdrop-blur-md shadow-lg hover:border-white/15 transition-all group cursor-pointer">
            <div className="w-7 h-7 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-400 mb-2">
              <FileCode2 className="w-3.5 h-3.5" />
            </div>
            <h3 className="text-xs font-semibold text-white group-hover:text-amber-300 transition-colors">
              Codebase Understanding
            </h3>
            <p className="mt-1 text-[11px] text-zinc-400 leading-snug">
              Get a clear overview of your project structure and logic.
            </p>
          </div>

          {/* Card 2: Deep Analysis */}
          <div className="rounded-2xl bg-[#192523]/80 border border-white/[0.08] p-3.5 backdrop-blur-md shadow-lg hover:border-white/15 transition-all group cursor-pointer">
            <div className="w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 mb-2">
              <Search className="w-3.5 h-3.5" />
            </div>
            <h3 className="text-xs font-semibold text-white group-hover:text-amber-300 transition-colors">
              Deep Analysis
            </h3>
            <p className="mt-1 text-[11px] text-zinc-400 leading-snug">
              Find issues, dependencies and improvement opportunities.
            </p>
          </div>

          {/* Card 3: Generate & Modify */}
          <div className="rounded-2xl bg-[#192523]/80 border border-white/[0.08] p-3.5 backdrop-blur-md shadow-lg hover:border-white/15 transition-all group cursor-pointer">
            <div className="w-7 h-7 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400 mb-2">
              <FileEdit className="w-3.5 h-3.5" />
            </div>
            <h3 className="text-xs font-semibold text-white group-hover:text-amber-300 transition-colors">
              Generate & Modify
            </h3>
            <p className="mt-1 text-[11px] text-zinc-400 leading-snug">
              Ask for changes, refactors or new features.
            </p>
          </div>

          {/* Card 4: Research & Learn */}
          <div className="rounded-2xl bg-[#192523]/80 border border-white/[0.08] p-3.5 backdrop-blur-md shadow-lg hover:border-white/15 transition-all group cursor-pointer">
            <div className="w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400 mb-2">
              <Sparkles className="w-3.5 h-3.5" />
            </div>
            <h3 className="text-xs font-semibold text-white group-hover:text-amber-300 transition-colors">
              Research & Learn
            </h3>
            <p className="mt-1 text-[11px] text-zinc-400 leading-snug">
              Turn any repo into a personal documentation assistant.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
