"use client";

import React, { useSyncExternalStore } from "react";
import HeroClouds from "@/components/clouds/HeroClouds";
import IntroGate from "@/components/intro/IntroGate";
import HeroCue from "@/components/motion/HeroCue";
import Magnetic from "@/components/motion/Magnetic";
import RollText from "@/components/motion/RollText";
import SmoothPage from "@/components/motion/SmoothPage";
import ThemeToggle from "@/components/theme/ThemeToggle";

import ZeroTrustGuarantee from "@/components/landing/ZeroTrustGuarantee";
import SetupJourney from "@/components/landing/SetupJourney";
import InteractiveFindingPreview from "@/components/landing/InteractiveFindingPreview";
import TwoClocksEngine from "@/components/landing/TwoClocksEngine";
import PipelineVisualizer from "@/components/landing/PipelineVisualizer";
import AuditDropzone from "@/components/landing/AuditDropzone";
import LandingFooter from "@/components/landing/LandingFooter";

// Header stays transparent (options only) until the page has been scrolled this far.
const HEADER_SOLID_AFTER_PX = 24;

const subscribeToScroll = (onChange: () => void) => {
  window.addEventListener("scroll", onChange, { passive: true });
  return () => window.removeEventListener("scroll", onChange);
};
const getScrolled = () => window.scrollY > HEADER_SOLID_AFTER_PX;
const getServerScrolled = () => false;

export default function Home() {
  const scrolled = useSyncExternalStore(subscribeToScroll, getScrolled, getServerScrolled);

  const scrollToAudit = () => {
    const el = document.getElementById("audit");
    if (el) {
      el.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <div className="min-h-screen bg-page text-ink selection:bg-accent selection:text-on-accent">
      {/* Light theme sky. It comes before the tree in the DOM, so it paints underneath it. */}
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />

      <IntroGate />

      {/* Glow Effects (night only) */}
      <div className="pointer-events-none fixed -top-40 -left-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div className="pointer-events-none fixed top-1/3 -right-40 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />

      {/* Navigation */}
      <header
        className={`fixed inset-x-0 top-0 z-50 border-b transition-[background-color,border-color,backdrop-filter] duration-300 ${
          scrolled
            ? "border-line bg-page/80 backdrop-blur-md"
            : "border-transparent bg-transparent"
        }`}
      >
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          {/* Brand stays in layout (so the options remain right-aligned) but is hidden until scroll. */}
          <div
            className={`flex items-center gap-3 transition-opacity duration-300 ${
              scrolled ? "opacity-100" : "pointer-events-none opacity-0"
            }`}
            aria-hidden={!scrolled}
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 shadow-lg shadow-emerald-500/20">
              <svg className="h-6 w-6 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-lg tracking-tight text-ink">EcoAudit</span>
                <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent-strong border border-accent-line">
                  AWS Environmental Hacks
                </span>
              </div>
              <p className="text-xs text-ink-3">Software Sustainability & Compute Waste Auditor</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <ThemeToggle />
            <Magnetic>
              <a
                href="https://github.com/AWS-env/environmental-hacks"
                target="_blank"
                rel="noreferrer"
                className="group flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
              >
                <RollText>GitHub Repo</RollText>
              </a>
            </Magnetic>
            <Magnetic>
              <button
                type="button"
                id="upload-report-btn"
                onClick={scrollToAudit}
                className="group rounded-lg bg-accent px-4 py-1.5 text-xs font-semibold text-on-accent shadow-md shadow-accent/20 transition-all hover:bg-accent-hover active:scale-95"
              >
                <RollText>Upload report.json</RollText>
              </button>
            </Magnetic>
          </div>
        </div>
      </header>

      {/* Smooth scrolling applies to everything inside */}
      <SmoothPage>
        {/* First screen: the tree (IntroGate) and clouds live here */}
        <div className="relative h-svh">
          <HeroClouds />
          <HeroCue />
        </div>

        {/* Core Architecture-Flow Sections */}
        <ZeroTrustGuarantee />
        <SetupJourney />
        <InteractiveFindingPreview />
        <TwoClocksEngine />
        <PipelineVisualizer />
        <AuditDropzone />
        <LandingFooter />
      </SmoothPage>
    </div>
  );
}
