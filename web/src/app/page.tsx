"use client";

import React from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { useRouteTransition } from "@/components/transition/RouteTransition";
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
  const transition = useRouteTransition();
  return (
    <div className="min-h-screen bg-page text-ink selection:bg-accent selection:text-on-accent">
      {/* Light theme sky. It comes before the tree in the DOM, so it paints underneath it. */}
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />

      <IntroGate />

      {/* Glow Effects (night only) */}
      <div className="pointer-events-none fixed -top-40 -left-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div className="pointer-events-none fixed top-1/3 -right-40 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />

      {/* Smooth scrolling applies to everything inside */}
      <SmoothPage>
        {/* First screen: the tree (IntroGate) and clouds live here */}
        <div className="relative h-svh">
          <HeroClouds />
          <Link
            href="/home"
            onNavigate={(event) => transition?.intercept(event, "/home")}
            className="landing-continue group absolute right-6 top-6 z-30 inline-flex items-center gap-6 rounded-full border border-line bg-surface px-6 py-3 text-xs font-medium uppercase tracking-[0.18em] text-ink shadow-sm backdrop-blur-xl transition-colors hover:border-accent-line hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent md:right-10 md:top-10"
          >
            Continue
            <span aria-hidden="true" className="grid h-7 w-7 place-items-center rounded-full border border-line transition-colors group-hover:border-accent-line group-hover:text-accent-strong"><ArrowUpRight size={15} /></span>
          </Link>
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
