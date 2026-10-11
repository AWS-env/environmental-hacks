"use client";

import React from "react";
import Sidebar from "@/components/Sidebar";
import HeroClouds from "@/components/clouds/HeroClouds";
import IntroGate from "@/components/intro/IntroGate";
import HeroCue from "@/components/motion/HeroCue";
import SmoothPage from "@/components/motion/SmoothPage";

import ZeroTrustGuarantee from "@/components/landing/ZeroTrustGuarantee";
import SetupJourney from "@/components/landing/SetupJourney";
import InteractiveFindingPreview from "@/components/landing/InteractiveFindingPreview";
import TwoClocksEngine from "@/components/landing/TwoClocksEngine";
import PipelineVisualizer from "@/components/landing/PipelineVisualizer";
import AuditDropzone from "@/components/landing/AuditDropzone";
import LandingFooter from "@/components/landing/LandingFooter";

export default function Home() {
  return (
    <div className="min-h-screen bg-page text-ink selection:bg-accent selection:text-on-accent">
      {/* Light theme sky. It comes before the tree in the DOM, so it paints underneath it. */}
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />

      {/* Global Sleek Floating Capsule Sidebar */}
      <Sidebar />

      <IntroGate />

      {/* Glow Effects (night only) */}
      <div className="pointer-events-none fixed -top-40 -left-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div className="pointer-events-none fixed top-1/3 -right-40 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />

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
